"""
can-this-be-redeveloped / 요건 모듈 (A) — "이 지역·집이 재개발 될 수 있나"

정비구역 지정 요건(노후도·연한 등)을 충족해서 재개발이 '될 수 있는지'를 판정한다.
※ '될 수 있나(can)' = 요건/자격. '될까(will)' = 예측 → 우리는 예측 안 함.
   조합 갈등·정치·시장이 좌우하는 실현 여부는 판정 대상 아님.

자매 모듈: succession(engine.py) = "내가 사서 입주권 자격 되나(§39②/§37)".
둘 다 통과해야 실제로 새 집을 받음.

설계 규율은 succession 과 동일: 4값(MET/NOT_MET/INSUFFICIENT/CONFLICT),
근거등급(P0>P1>S1>T), 미검증 기준은 추측 금지→확인필요, source_span, 확인필요 반올림 금지.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class V(Enum):
    MET = "MET"; NOT_MET = "NOT_MET"; INSUFFICIENT = "INSUFFICIENT"
    CONFLICT = "CONFLICT"; NA = "N/A"


class Grade(Enum):
    P0 = "P0"   # 조문·조례
    P1 = "P1"   # 원본(건축물대장·항공 노후도 조사)
    S1 = "S1"   # 기관게시(정보몽땅 노후도 표시)
    T = "T"     # 진술
    U = "U"


# ── 기준값(조례 의존 — 하드코딩 금지, 여기만 수정) ──
class Cfg:
    REGION = "서울"
    # 노후·불량건축물 경과연수: 준공 후 20~30년 범위 시·도 조례. RC/철골조 공동주택 통상 30년.
    OLD_YEARS_RC = 30
    OLD_YEARS_ETC = 30            # 조례 20~30, 서울 30 가정
    OLD_YEARS_VERIFIED = True     # 범위·RC30 웹 교차확인(law.go.kr 조례 원문 최종확인 권장)
    OLD_YEARS_SRC = "도정법 §2·시행령 §2 / 시·도 조례(준공 후 20~30년, RC 공동주택 30년)"

    # 재개발(주택정비형) 지역 노후도 요건 — 서울 조례
    REDEV_RATIO = 0.60           # 노후·불량건축물 60% 이상
    REDEV_RATIO_PROMO = 0.50     # 재정비촉진지구 50%
    REDEV_RATIO_VERIFIED = True  # 서울시 도시정비조례(웹 확인)
    REDEV_RATIO_SRC = "서울특별시 도시 및 주거환경정비 조례(노후도 60%, 재정비촉진 50%)"

    # 선택요건(하나 이상) — 별표 수치가 조례별 상이 → 미검증
    SELECT_VERIFIED = False
    SELECT_SRC = "서울시 정비조례 별표(호수밀도·접도율·과소필지) — 원문 미검증"
    HOSU_DENSITY = 60            # ha당 동수 (미검증)
    GWASO_RATIO = 0.40           # 과소필지 40% 이상 (미검증)
    JEOPDO_MAX = 0.40            # 접도율 40% 이하 (미검증)


@dataclass
class Fact:
    value: object
    grade: Grade
    source_doc: str = ""
    source_span: str = ""


@dataclass
class Building:      # 내 건물 (참고: 노후 여부)
    준공일: Optional[Fact] = None       # date
    구조: Optional[str] = None          # "RC공동주택" | "기타"


@dataclass
class Area:          # 정비구역 (지정 핵심)
    사업유형: str = "재개발"            # "재개발" | "재건축"
    지역: str = "서울"
    재정비촉진지구: bool = False
    노후불량비율: Optional[Fact] = None  # 0~1
    호수밀도: Optional[Fact] = None      # ha당 동수
    과소필지비율: Optional[Fact] = None  # 0~1
    접도율: Optional[Fact] = None        # 0~1 (좁을수록 열악)


@dataclass
class Req:
    name: str
    verdict: V
    value: Optional[str] = None
    source_doc: Optional[str] = None
    source_span: Optional[str] = None
    grade: Grade = Grade.U
    missing_input: Optional[str] = None
    unverified: bool = False


# ── 판정 ──

def _building_old(b: Building) -> Req:
    if b.준공일 is None:
        return Req("내 건물 노후 여부(경과연수)", V.INSUFFICIENT, grade=Grade.U,
                   missing_input="건축물대장(준공일)")
    yrs = (date(2026, 9, 1) - b.준공일.value).days / 365.25   # 기준일 고정(결정론)
    need = Cfg.OLD_YEARS_RC if b.구조 == "RC공동주택" else Cfg.OLD_YEARS_ETC
    v = V.MET if yrs >= need else V.NOT_MET
    return Req("내 건물 노후 여부(경과연수)", v,
               value=f"준공 {b.준공일.value} → {yrs:.0f}년 (기준 {need}년)",
               source_doc=b.준공일.source_doc, source_span=b.준공일.source_span,
               grade=b.준공일.grade)


def _area_ratio(a: Area) -> Req:
    if a.사업유형 != "재개발":
        return Req("지역 노후도 비율", V.NA, value="재건축은 안전진단 트리(별도)", grade=Grade.U)
    need = Cfg.REDEV_RATIO_PROMO if a.재정비촉진지구 else Cfg.REDEV_RATIO
    if a.노후불량비율 is None:
        return Req("지역 노후도 비율(필수)", V.INSUFFICIENT, grade=Grade.U,
                   missing_input="정보몽땅 노후도 현황 또는 노후도 조사자료")
    r = a.노후불량비율.value
    v = V.MET if r >= need else V.NOT_MET
    return Req("지역 노후도 비율(필수)", v,
               value=f"노후·불량 {r:.0%} (기준 {need:.0%}"
                     + (" 재정비촉진" if a.재정비촉진지구 else "") + ")",
               source_doc=a.노후불량비율.source_doc, source_span=a.노후불량비율.source_span,
               grade=a.노후불량비율.grade)


def _select_one(a: Area) -> list[Req]:
    """선택요건 — 하나 이상 충족. 단 별표 수치 미검증(verified=False)이라 확인필요로."""
    out = []
    specs = [
        ("과소필지 비율", a.과소필지비율, Cfg.GWASO_RATIO, ">=",
         "정비계획 자료(과소필지 비율)"),
        ("접도율", a.접도율, Cfg.JEOPDO_MAX, "<=",
         "정비계획 자료(접도율)"),
        ("호수밀도(ha당 동수)", a.호수밀도, Cfg.HOSU_DENSITY, ">=",
         "정비계획 자료(호수밀도)"),
    ]
    for name, fact, thr, op, miss in specs:
        if fact is None:
            out.append(Req(name, V.INSUFFICIENT, grade=Grade.U, missing_input=miss))
            continue
        val = fact.value
        prov = (val >= thr) if op == ">=" else (val <= thr)
        # 기준 미검증 → 잠정치는 보여주되 확정 판정은 보류(확인필요)
        out.append(Req(name, V.INSUFFICIENT if Cfg.SELECT_VERIFIED is False
                       else (V.MET if prov else V.NOT_MET),
                       value=f"{val:.0%} {op} 기준 {thr:.0%} → 잠정 "
                             + ("충족" if prov else "미달"),
                       source_doc=fact.source_doc, source_span=fact.source_span,
                       grade=fact.grade,
                       missing_input=(Cfg.SELECT_SRC if Cfg.SELECT_VERIFIED is False else None),
                       unverified=Cfg.SELECT_VERIFIED is False))
    return out


@dataclass
class Report:
    building: Req
    area_ratio: Req
    selects: list[Req]
    overall: str
    scope: str = "재개발 '될 수 있나'(정비구역 지정 요건) 판정 — 실제 진행/실현 여부는 예측 안 함"
    notes: list[str] = field(default_factory=list)

    @property
    def 요청자료(self) -> list[str]:
        docs = []
        for r in [self.building, self.area_ratio, *self.selects]:
            if r.verdict == V.INSUFFICIENT and r.missing_input and r.missing_input not in docs:
                docs.append(r.missing_input)
        return docs


_OA = {"가능": "재개발 될 수 있음", "미달": "요건 미달", "확인": "확인 필요"}


def evaluate(b: Building, a: Area) -> Report:
    building = _building_old(b)
    ratio = _area_ratio(a)
    selects = _select_one(a)
    notes = []

    # 종합: 지역 노후도(필수) AND 선택요건 하나 이상. (내 건물 노후는 참고)
    if ratio.verdict == V.NOT_MET:
        overall = _OA["미달"]
        notes.append("지역 노후도가 지정 기준 미달 → 정비구역 지정 어려움(선행 필수요건).")
    elif ratio.verdict in (V.INSUFFICIENT, V.NA):
        overall = _OA["확인"]
    else:  # ratio MET
        sv = [s.verdict for s in selects]
        if V.MET in sv:
            overall = _OA["가능"]
        elif V.INSUFFICIENT in sv:
            overall = _OA["확인"]
        else:
            overall = _OA["미달"]
            notes.append("노후도는 충족하나 선택요건(호수밀도·접도율·과소필지) 미충족.")
    if not Cfg.SELECT_VERIFIED:
        notes.append("⚠️ 선택요건 별표 수치는 조례 원문 미검증 → 잠정치만 표시, 확정은 보류(확인필요).")
    return Report(building, ratio, selects, overall, notes=notes)


_ICON = {V.MET: "🟢MET", V.NOT_MET: "🔴NOT_MET", V.INSUFFICIENT: "🟡확인필요",
         V.CONFLICT: "🟠CONFLICT", V.NA: "⚪N/A"}


def render(rep: Report) -> str:
    head = {"재개발 될 수 있음": "🟢", "요건 미달": "🔴", "확인 필요": "🟡"}
    L = [f"■ 판정: {head.get(rep.overall,'')} {rep.overall}",
         f"  스코프: {rep.scope}", ""]
    def line(r):
        u = " ⚠️기준 미검증" if r.unverified else ""
        L.append(f"{_ICON[r.verdict]} {r.name}{u}: {r.value or ''}")
        if r.source_span:
            L.append(f"    └ 원문: {r.source_span} [{r.source_doc}]")
        if r.missing_input:
            L.append(f"    └ 필요: {r.missing_input}")
    L.append("[지역 요건 — 지정 핵심]")
    line(rep.area_ratio)
    for s in rep.selects:
        line(s)
    L.append("")
    L.append("[내 건물 — 참고]")
    line(rep.building)
    L.append("")
    if rep.요청자료:
        L.append("── 이 자료를 주시면 판정이 확정됩니다 ──")
        for d in rep.요청자료:
            L.append(f"  📄 {d}")
        L.append("")
    for n in rep.notes:
        L.append(n)
    return "\n".join(L)
