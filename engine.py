"""
입주권 신호등 — 재개발·재건축 조합원 지위 승계 판정 엔진

투기과열지구에서 정비사업 물건을 사면 조합원 지위를 '승계'받아 입주권을 얻거나,
못 받으면 '현금청산' 당한다(프리미엄+입주권 통째로 손실). 도시정비법 §39② +
시행령 §37 의 예외에 하나라도 걸리면 승계 가능.

이 엔진은 '읽는 법'이 아니라 출력 계약(output contract)이다.
─────────────────────────────────────────────────────────────
설계 규율 (스펙 그대로)
1. 4값 판정: MET / NOT_MET / INSUFFICIENT / CONFLICT.
   - INSUFFICIENT = 서류 없어서 판단 불가 → 사용자가 서류 주면 해결.
   - CONFLICT     = 자료가 서로 어긋남 → 전문가로 보냄.
2. 근거 등급: P0 조문 > P1 원본문서 > S1 기관게시 > T 당사자진술.
   T 단독으로는 절대 MET 을 못 만든다(코드에서 강제).
3. 논리: 승계가능 = OR(예외1..8), 각 예외 = AND(내부 요건).
   - 하나라도 MET → 승계가능(short-circuit)
   - 전부 NOT_MET → 승계불가(현금청산)
   - 전부 NOT_MET 인데 INSUFFICIENT/CONFLICT 섞임 → 확인필요 (절대 '불가'로 반올림 금지)
4. 판정 신뢰도 = min(구성요소 등급) — 최약 링크.
5. 모든 추출값에 원문 span(source_span) 병기 → 30초 반증 가능.
6. as-of 2개: 기준일(양수일, 판정 시점) + doc_asof(문서 발급일→신선도).
7. 스코프: 조합원 지위 승계 '가능성'만. 감정가·청산금·분담금·투자가치는 판정 안 함.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


# ───────────────────────── 타입 ─────────────────────────

class V(Enum):
    MET = "MET"
    NOT_MET = "NOT_MET"
    INSUFFICIENT = "INSUFFICIENT"   # 사용자가 서류로 해결 가능
    CONFLICT = "CONFLICT"           # 전문가로 보내야 함
    NA = "N/A"                      # 이 사업유형엔 해당 없는 예외


class Grade(Enum):
    P0 = "P0"   # 조문 (요건 자체)
    P1 = "P1"   # 원본 문서 (등기부·초본·정보몽땅 고시)
    S1 = "S1"   # 기관 게시 정보 (정보몽땅 화면 단계표시 등, 원문고시 아님)
    T = "T"     # 당사자 진술 (매도인·중개사·조합 구두) — 단독 MET 금지
    U = "U"     # 없음/미상


_GRADE_ORDER = {Grade.P0: 4, Grade.P1: 3, Grade.S1: 2, Grade.T: 1, Grade.U: 0}


def _min_grade(grades: list[Grade]) -> Grade:
    if not grades:
        return Grade.U
    return min(grades, key=lambda g: _GRADE_ORDER[g])


STALE_DAYS = 30  # 등기부·초본 발급이 기준일보다 이만큼 전이면 stale 경고


# ───────────────────────── 입력 ─────────────────────────

@dataclass
class Fact:
    """추출된 사실 하나. value=None 은 '확인 결과 없음(미신청/미착공 등 확정)'을 뜻함.
    Case 에 아예 키가 없으면(=None Fact) '미확인'."""
    value: object
    grade: Grade
    source_doc: str = ""
    source_span: str = ""
    doc_asof: Optional[date] = None


@dataclass
class Case:
    사업유형: str                       # "재건축" | "재개발"
    기준일: date                         # 양수일 (판정 시점)
    기준일_기준: str = "미정"            # "계약일" | "등기일" | "미정"
    투기과열지구: Optional[bool] = None  # None=미확인

    취득일: Optional[Fact] = None        # 등기부 갑구 소유권 접수일 (date)
    거주개시일: Optional[Fact] = None    # 초본상 실거주 개시일 (합산 반영, date)
    조합설립인가일: Optional[Fact] = None
    사업시행계획인가일: Optional[Fact] = None   # value=None → 확정 미신청
    착공일: Optional[Fact] = None                # value=None → 확정 미착공
    준공: Optional[Fact] = None                  # value=bool
    조합명부_등재일: Optional[Fact] = None       # CONFLICT 교차검증용

    # 특수정황 주장(예외 2·3·4·8). None=주장없음(→NOT_MET), Fact=주장/증빙
    claim_세대이전: Optional[Fact] = None
    claim_상속: Optional[Fact] = None
    claim_해외이주: Optional[Fact] = None
    claim_불가피: Optional[Fact] = None   # 경매·공매 등 시행령 §37③


# ───────────────────────── 판정 단위 ─────────────────────────

@dataclass
class Req:
    name: str
    verdict: V
    value: Optional[str] = None
    source_doc: Optional[str] = None
    source_span: Optional[str] = None
    grade: Grade = Grade.U
    stale: bool = False
    missing_input: Optional[str] = None   # INSUFFICIENT 일 때 요청할 서류


@dataclass
class ExResult:
    id: str
    label: str
    law: str
    applies_to: str          # "공통" | "재건축"
    verified: bool           # 조문 검증 여부 (규칙 자체)
    verdict: V
    grade: Grade
    reqs: list[Req]


# ───────────────────────── 헬퍼 ─────────────────────────

def _years(d_from: date, d_to: date) -> float:
    return (d_to - d_from).days / 365.25


def _stale(f: Fact, 기준일: date) -> bool:
    if not f or not f.doc_asof:
        return False
    return (기준일 - f.doc_asof).days > STALE_DAYS


def _req_duration(name, fact, 기준일, need_years, missing_doc):
    """소유/거주/경과 기간형 요건. 최약링크(T 단독 MET 금지) 포함."""
    if fact is None:
        return Req(name, V.INSUFFICIENT, grade=Grade.U, missing_input=missing_doc)
    if fact.value is None:  # 확정 미상(문서엔 있으나 값 공란) — 보수적으로 INSUFFICIENT
        return Req(name, V.INSUFFICIENT, grade=fact.grade, missing_input=missing_doc)
    yrs = _years(fact.value, 기준일)
    met = yrs >= need_years
    verdict = V.MET if met else V.NOT_MET
    stale = _stale(fact, 기준일)
    r = Req(name, verdict,
            value=f"{fact.value} → {yrs:.1f}년 (기준 {need_years}년)",
            source_doc=fact.source_doc, source_span=fact.source_span,
            grade=fact.grade, stale=stale)
    # T 단독으로는 MET 불가
    if r.verdict == V.MET and r.grade == Grade.T:
        r.verdict = V.INSUFFICIENT
        r.missing_input = f"{missing_doc} (구두진술만으론 확정 불가)"
    return r


def _combine_and(reqs: list[Req]) -> tuple[V, Grade]:
    vs = [r.verdict for r in reqs]
    if V.NOT_MET in vs:
        v = V.NOT_MET                 # AND: 하나라도 불충족이면 확정 불충족
    elif V.CONFLICT in vs:
        v = V.CONFLICT
    elif V.INSUFFICIENT in vs:
        v = V.INSUFFICIENT
    else:
        v = V.MET
    grade = _min_grade([r.grade for r in reqs])   # 최약 링크
    return v, grade


# ───────────────────────── 예외 빌더 (8개) ─────────────────────────

def _ex1_장기보유(c: Case) -> ExResult:
    소유 = _req_duration("소유 10년 이상", c.취득일, c.기준일, 10,
                        "매도인 등기부등본(갑구 소유권 접수일)")
    # CONFLICT: 등기부 취득일 ≠ 조합명부 등재일
    if (c.취득일 and c.취득일.value and c.조합명부_등재일 and c.조합명부_등재일.value
            and c.취득일.value != c.조합명부_등재일.value):
        소유 = Req("소유 10년 이상", V.CONFLICT,
                  value=f"등기부 {c.취득일.value} ≠ 조합명부 {c.조합명부_등재일.value}",
                  source_doc="등기부 vs 조합명부", grade=Grade.P1,
                  missing_input="등기부·조합명부 불일치 — 전문가 확인 필요")
    거주 = _req_duration("거주 5년 이상(합산)", c.거주개시일, c.기준일, 5,
                        "매도인 주민등록초본(주소 변동 포함)")
    verdict, grade = _combine_and([소유, 거주])
    return ExResult("ex1_장기보유", "1세대1주택 장기보유(10년 소유·5년 거주)",
                    "도시정비법 §39②4호 · 시행령 §37①", "공통", True,
                    verdict, grade, [소유, 거주])


def _ex_재건축_기간(c: Case, exid, label, law, anchor_fact, anchor_name,
                  후속_fact, 후속_없음이_충족: bool, 후속_doc):
    """재건축 전용 3종(사업시행인가 미신청/미착공/미준공)의 공통 뼈대.
    anchor+3년 경과 & 후속행위 없음 & 소유 3년 이상 → 충족."""
    if c.사업유형 != "재건축":
        na = Req(label, V.NA, grade=Grade.U)
        return ExResult(exid, label, law, "재건축", True, V.NA, Grade.U, [na])

    reqs = []
    # 요건 A: anchor 로부터 3년 경과했는데 후속행위 없음
    if anchor_fact is None:
        reqs.append(Req(f"{anchor_name} 후 3년 경과", V.INSUFFICIENT, grade=Grade.U,
                        missing_input="정보몽땅 사업단계 고시(구역·인가일)"))
    elif anchor_fact.value is None:
        # 선행 단계(인가·착공) 자체가 확정적으로 없음 → 이 예외는 성립 불가(불충족)
        reqs.append(Req(f"{anchor_name} 선행단계 미도래", V.NOT_MET,
                        value="해당 인가/착공 없음 → 이 예외 성립 불가",
                        grade=anchor_fact.grade))
    else:
        passed3 = _years(anchor_fact.value, c.기준일) >= 3
        if 후속_fact is None:
            # 후속행위 발생 여부 자체가 미확인
            reqs.append(Req(f"{anchor_name}+3년 내 후속행위 없음", V.INSUFFICIENT,
                            grade=Grade.U, missing_input=후속_doc))
        else:
            후속있음 = 후속_fact.value is not None
            cond = passed3 and (not 후속있음)
            reqs.append(Req(f"{anchor_name}+3년 내 후속행위 없음",
                            V.MET if cond else V.NOT_MET,
                            value=f"{anchor_name} {anchor_fact.value}, 후속 "
                                  + ("없음" if not 후속있음 else str(후속_fact.value)),
                            source_doc=anchor_fact.source_doc,
                            source_span=anchor_fact.source_span,
                            grade=_min_grade([anchor_fact.grade, 후속_fact.grade])))
    # 요건 B: 소유 3년 이상
    reqs.append(_req_duration("소유 3년 이상", c.취득일, c.기준일, 3,
                             "매도인 등기부등본(갑구 소유권 접수일)"))
    verdict, grade = _combine_and(reqs)
    return ExResult(exid, label, law, "재건축", True, verdict, grade, reqs)


def _ex_특수정황(exid, label, law, claim: Optional[Fact], proof_doc) -> ExResult:
    """예외 2·3·4·8. 주장 없으면 정황 없음 → NOT_MET.
    T 진술만 있으면 INSUFFICIENT(원본 필요). P1 증빙이면 MET."""
    if claim is None:
        r = Req(label, V.NOT_MET, value="해당 정황 없음", grade=Grade.U)
    elif claim.grade == Grade.T:
        r = Req(label, V.INSUFFICIENT, value="주장만 있음(구두)",
                grade=Grade.T, missing_input=proof_doc)
    else:
        r = Req(label, V.MET, value=str(claim.value),
                source_doc=claim.source_doc, source_span=claim.source_span,
                grade=claim.grade)
    return ExResult(exid, label, law, "공통", claim is None or claim.grade != Grade.U,
                    r.verdict, r.grade, [r])


# 예외 8 (시행령 §37③ 불가피 사유)은 조문 각 호가 아직 원문 미검증 → verified=False
def _ex8_불가피(c: Case) -> ExResult:
    res = _ex_특수정황("ex8_불가피", "불가피한 사유(경매·공매 등)",
                     "시행령 §37③ 각 호 (원문 미검증)", c.claim_불가피,
                     "해당 사유 증빙(경매개시결정·판결문 등)")
    res.verified = False  # 각 호 열거 law.go.kr 확인 전까지
    return res


# ───────────────────────── 종합 ─────────────────────────

_OVERALL = {"가능": "승계가능", "불가": "승계불가(현금청산 대상)", "확인": "확인필요"}


@dataclass
class Report:
    case: Case
    exceptions: list[ExResult]
    overall: str
    scope: str = "조합원 지위 승계 '가능성'만 판정 — 감정가·청산금·분담금·투자가치는 다루지 않음"
    notes: list[str] = field(default_factory=list)

    @property
    def 요청서류(self) -> list[str]:
        docs = []
        for ex in self.exceptions:
            for r in ex.reqs:
                if r.verdict == V.INSUFFICIENT and r.missing_input:
                    if r.missing_input not in docs:
                        docs.append(r.missing_input)
        return docs

    @property
    def 확인실패로그(self) -> list[str]:
        logs = []
        for ex in self.exceptions:
            for r in ex.reqs:
                if r.verdict == V.CONFLICT and r.missing_input:
                    logs.append(f"[CONFLICT] {ex.id}/{r.name}: {r.missing_input}")
        return logs


def evaluate(c: Case) -> Report:
    notes = []
    # 기준일 설계-U: 양수 시점(계약일 vs 등기일)이 8예외 전부의 계산 기준
    if c.기준일_기준 == "미정":
        notes.append("⚠️ 기준일(양수 시점)이 계약일인지 등기일인지 미확정. "
                     "이 날짜 하나가 모든 예외의 기간계산 기준이라, 계약일/등기일 두 "
                     "시나리오를 각각 돌려 결과가 갈리는지 확인할 것(시행령 §37③6 유권해석 필요).")

    # 투기과열지구 게이트 — 지정 아니면 양도 자유
    if c.투기과열지구 is False:
        return Report(c, [], _OVERALL["가능"],
                      notes=notes + ["투기과열지구 아님 → 조합원 지위 양도 제한 없음."])
    if c.투기과열지구 is None:
        notes.append("투기과열지구 지정 여부 미확인(정보몽땅/지정고시). 지정 가정하고 판정함.")

    exceptions = [
        _ex1_장기보유(c),
        _ex_특수정황("ex2_세대이전", "세대원 전원 이전(근무·질병·취학 등)",
                   "도시정비법 §39②1호", c.claim_세대이전,
                   "전원 전출 증명(초본·재직/재학/진단 증빙)"),
        _ex_특수정황("ex3_상속", "상속주택 세대원 전원 이전",
                   "도시정비법 §39②2호", c.claim_상속,
                   "가족관계증명·상속 확인 서류"),
        _ex_특수정황("ex4_해외이주", "세대원 전원 해외이주/2년+ 체류",
                   "도시정비법 §39②3호", c.claim_해외이주,
                   "해외이주신고확인서·출입국 사실증명"),
        _ex_재건축_기간(c, "ex5_사업시행인가지연", "재건축: 조합설립+3년 내 사업시행인가 미신청",
                     "시행령 §37②", c.조합설립인가일, "조합설립인가",
                     c.사업시행계획인가일, True, "정보몽땅 사업시행계획인가 신청 이력"),
        _ex_재건축_기간(c, "ex6_착공지연", "재건축: 사업시행인가+3년 내 미착공",
                     "시행령 §37②", c.사업시행계획인가일, "사업시행계획인가",
                     c.착공일, True, "정보몽땅 착공 이력"),
        _ex_재건축_기간(c, "ex7_준공지연", "재건축: 착공+3년 내 미준공",
                     "시행령 §37②", c.착공일, "착공",
                     c.준공, True, "정보몽땅 준공 이력"),
        _ex8_불가피(c),
    ]

    # OR 종합 — 하나라도 MET 이면 가능(short-circuit), 절대 확인필요를 불가로 반올림 금지
    pool = [e for e in exceptions if e.verdict != V.NA]
    if any(e.verdict == V.MET for e in pool):
        overall = _OVERALL["가능"]
    elif any(e.verdict in (V.INSUFFICIENT, V.CONFLICT) for e in pool):
        overall = _OVERALL["확인"]
    else:
        overall = _OVERALL["불가"]
    return Report(c, exceptions, overall, notes=notes)


# ───────────────────────── 출력 ─────────────────────────

def to_dict(rep: Report) -> dict:
    def gname(g): return g.value
    return {
        "scope": rep.scope,
        "기준일": str(rep.case.기준일),
        "기준일_기준": rep.case.기준일_기준,
        "사업유형": rep.case.사업유형,
        "판정": rep.overall,
        "예외별": [{
            "id": e.id, "근거": e.law, "적용": e.applies_to,
            "조문검증": e.verified, "verdict": e.verdict.value,
            "grade": gname(e.grade),
            "요건": [{
                "name": r.name, "verdict": r.verdict.value,
                "value": r.value, "grade": gname(r.grade),
                "source_doc": r.source_doc, "source_span": r.source_span,
                "stale": r.stale, "missing_input": r.missing_input,
            } for r in e.reqs],
        } for e in rep.exceptions],
        "요청서류": rep.요청서류,
        "확인실패로그": rep.확인실패로그,
        "notes": rep.notes,
    }


_ICON = {V.MET: "🟢MET", V.NOT_MET: "🔴NOT_MET", V.INSUFFICIENT: "🟡INSUFFICIENT",
         V.CONFLICT: "🟠CONFLICT", V.NA: "⚪N/A"}


def render(rep: Report) -> str:
    head = {"승계가능": "🟢", "승계불가(현금청산 대상)": "🔴", "확인필요": "🟡"}
    L = [f"■ 판정: {head.get(rep.overall,'')} {rep.overall}",
         f"  스코프: {rep.scope}",
         f"  기준일: {rep.case.기준일} ({rep.case.기준일_기준}) · 사업유형: {rep.case.사업유형}", ""]
    for e in rep.exceptions:
        if e.verdict == V.NA:
            continue
        vf = "" if e.verified else "  ⚠️조문 미검증"
        L.append(f"{_ICON[e.verdict]} [{e.grade.value}] {e.id} — {e.label}{vf}")
        L.append(f"    근거: {e.law} ({e.applies_to})")
        for r in e.reqs:
            tag = _ICON[r.verdict]
            extra = " ⏳stale" if r.stale else ""
            L.append(f"      · {tag} {r.name}{extra}: {r.value or ''}")
            if r.source_span:
                L.append(f"          └ 원문: {r.source_span} [{r.source_doc}]")
            if r.missing_input:
                L.append(f"          └ 필요: {r.missing_input}")
        L.append("")
    if rep.요청서류:
        L.append("── 이 서류만 주시면 판정이 확정됩니다 ──")
        for d in rep.요청서류:
            L.append(f"  📄 {d}")
        L.append("")
    if rep.확인실패로그:
        L.append("── 전문가 확인 필요(자료 충돌) ──")
        for x in rep.확인실패로그:
            L.append(f"  ⚠️ {x}")
        L.append("")
    for n in rep.notes:
        L.append(n)
    return "\n".join(L)
