"""
can-this-be-redeveloped / 요건 모듈 (A) — "이 지역·집이 재개발 될 수 있나"

정비구역 지정 요건(노후도·연한·면적 등)을 충족해서 재개발이 '될 수 있는지' 판정.
※ '될 수 있나(can)' = 요건/자격. '될까(will)' = 예측 → 우리는 예측 안 함.

자매 모듈: succession(engine.py) = "내가 사서 입주권 자격 되나(§39②/§37)".
둘 다 통과해야 실제로 새 집을 받음.

설계 규율은 succession 과 동일: 4값(MET/NOT_MET/INSUFFICIENT/CONFLICT),
근거등급(P0>P1>S1>T), 미검증 기준은 추측 금지→확인필요, source_span, 확인필요 반올림 금지.

수치 출처: 도시정비법 시행령 별표1 제2호(정비계획의 입안대상지역) + 서울특별시
도시정비조례 제6조제1항제2호·제2조. 조례 '별표1' 은 공동주택 노후기간표라 요건표가
아니다 — 한동안 이걸 요건 출처로 적어놨었다.
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


# ── 기준값(서울 조례 — 검증됨. 타 지자체는 조례 상이) ──
class Cfg:
    REGION = "서울"
    # 노후·불량건축물 경과연수: 준공 후 20~30년 범위 시·도 조례. 서울 RC 공동주택 30년.
    OLD_YEARS_RC = 30
    OLD_YEARS_ETC = 30
    OLD_YEARS_SRC = "도정법 §2·시행령 §2 / 시·도 조례(20~30년, RC 공동주택 30년)"

    # 주택정비형 재개발 지정요건 (영 별표1 제2호 + 조례 §6①2 — 원문 대조 검증됨)
    REDEV_RATIO = 0.60           # [필수] 노후·불량건축물 동수 비율 ≥ 60%
    REDEV_RATIO_PROMO = 0.50     #        재정비촉진지구 50%
    MIN_AREA = 10_000            # [필수] 구역 면적 ≥ 1만㎡ (심의 인정 시 5천㎡ 완화)
    MIN_AREA_RELAXED = 5_000
    # [선택] 아래 중 1개 이상
    GWASO_RATIO = 0.40           # 과소필지 ≥ 40%   (조례 §2⑨ — 토지면적 90㎡ 미만)
    JEOPDO_MAX = 0.40            # 주택접도율 ≤ 40%  (조례 §2⑩ — 폭4m 도로에 4m 접)
    HOSU_DENSITY = 60            # 호수밀도 ≥ 60/ha  (조례 §2⑤ — 1ha당 건축물 동수)
    NOHU_AREA_RATIO = 0.60       # 노후·불량건축물 연면적 ≥ 60%
    BANJIHA_RATIO = 0.50         # 반지하 ≥ 1/2   (영 별표1 제2호아목)
    # 서울시 운영기준 — 노후 동수가 이 선을 넘으면 선택요건을 갖춘 것으로 본다.
    # 조례 조문이 아니라 기관 게시(S1): 관악구 고시 후보지모집안내문(2024-10-02)
    #   "※ 노후동수 75%이상인 경우 : 시 조례(선택요건) 갖춘 것으로 봄
    #      (선택요건 미충족시에도 입안가능)"
    NOHU_WAIVER = 0.75
    NOHU_WAIVER_SRC = "서울시 운영기준 — 관악구 고시 후보지모집안내문(2024-10-02)"
    # 영 별표1 제2호는 목이 가~자 9개다. 우리가 값으로 재는 건 그중 일부라,
    # 재본 것이 다 미달이어도 '요건 미달'을 선언할 수 없다.
    SELECT_UNMODELED = ("다목 인구·산업 과도집중", "라목 최저고도지구",
                        "마목 공해 공업지역", "바목 역세권 고도이용",
                        "사목 방재지구 1/2 이상", "자목 제1호라·마목")
    SRC = ("도시정비법 시행령 별표1 제2호 + 서울시 도시정비조례 §6①2·§2 "
           "(주택정비형 재개발 지정요건)")


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
    사업유형: str = "재개발"
    지역: str = "서울"
    재정비촉진지구: bool = False
    # 이미 정비구역으로 지정·고시된 경우 — 지정요건은 행정청이 이미 인정한 것이라
    # 노후도·면적·선택요건을 우리가 다시 추정할 필요가 없다(요건 심사 종료).
    지정고시: Optional[Fact] = None
    면적: Optional[Fact] = None          # ㎡ [필수]
    노후불량비율: Optional[Fact] = None  # 0~1 [필수]
    과소필지비율: Optional[Fact] = None  # 0~1 [선택]
    접도율: Optional[Fact] = None        # 0~1 [선택] 낮을수록 열악
    호수밀도: Optional[Fact] = None      # ha당 건축물 동수 (조례 §2⑤) [선택]
    노후연면적비율: Optional[Fact] = None # 0~1 [선택]
    반지하비율: Optional[Fact] = None    # 0~1 [선택] 영 별표1 제2호아목
                                        # 표제부엔 지하층 '용도'가 없어 보통 None
    # 노후도를 잰 단위가 '정비구역 경계'가 아니라 법정동·도로·지번블록인 경우.
    # 값 자체는 전수 실측이지만 측정 대상이 요건의 그것과 달라 결론을 확정하지 않는다.
    노후도_대리지표: bool = False


@dataclass
class Req:
    name: str
    verdict: V
    value: Optional[str] = None
    source_doc: Optional[str] = None
    source_span: Optional[str] = None
    grade: Grade = Grade.U
    missing_input: Optional[str] = None
    kind: str = "필수"   # "필수" | "선택" | "참고" | "지정"
    provisional: bool = False   # 값은 실측이나 '측정 대상'이 요건의 그것과 달라 확정 불가


# ── 판정 헬퍼 ──

_BASE = date(2026, 9, 1)   # 기준일 고정(결정론)


def _from_fact(name, fact, thr, op, unit, miss, kind):
    if fact is None:
        return Req(name, V.INSUFFICIENT, grade=Grade.U, missing_input=miss, kind=kind)
    val = fact.value
    ok = (val >= thr) if op == ">=" else (val <= thr)
    if unit == "pct":
        vs = f"{val:.0%} {op} 기준 {thr:.0%}"
    elif unit == "num":
        vs = f"{val:.0f}동 {op} 기준 {thr:.0f}동"
    else:
        vs = f"{val:,.0f}㎡ {op} 기준 {thr:,.0f}㎡"
    return Req(name, V.MET if ok else V.NOT_MET, value=vs + (" ✓" if ok else " ✗"),
               source_doc=fact.source_doc, source_span=fact.source_span,
               grade=fact.grade, kind=kind)


def _building_old(b: Building) -> Req:
    if b.준공일 is None:
        return Req("내 건물 노후 여부(경과연수)", V.INSUFFICIENT, grade=Grade.U,
                   missing_input="건축물대장(준공일)", kind="참고")
    yrs = (_BASE - b.준공일.value).days / 365.25
    need = Cfg.OLD_YEARS_RC if b.구조 == "RC공동주택" else Cfg.OLD_YEARS_ETC
    return Req("내 건물 노후 여부(경과연수)", V.MET if yrs >= need else V.NOT_MET,
               value=f"준공 {b.준공일.value} → {yrs:.1f}년 (기준 {need}년)",
               source_doc=b.준공일.source_doc, source_span=b.준공일.source_span,
               grade=b.준공일.grade, kind="참고")


def _designated(a: Area) -> Req:
    """이미 지정·고시됨 → 지정요건 충족을 행정청이 인정한 상태."""
    f = a.지정고시
    return Req("정비구역 지정 여부", V.MET, value=str(f.value),
               source_doc=f.source_doc, source_span=f.source_span,
               grade=f.grade, kind="필수")


def _area_ratio(a: Area) -> Req:
    if a.사업유형 != "재개발":
        return Req("지역 노후도 비율", V.NA, value="재건축은 안전진단 트리(별도)", kind="필수")
    need = Cfg.REDEV_RATIO_PROMO if a.재정비촉진지구 else Cfg.REDEV_RATIO
    r = _from_fact("지역 노후도 비율", a.노후불량비율, need, ">=", "pct",
                   "정보몽땅 노후도 현황 또는 노후도 조사자료", "필수")
    return r


def _area_size(a: Area) -> Req:
    if a.면적 is None:
        return Req("정비구역 면적", V.INSUFFICIENT, grade=Grade.U,
                   missing_input="정비계획 자료(구역 면적)", kind="필수")
    m = a.면적.value
    if m >= Cfg.MIN_AREA:
        vs, v = f"{m:,.0f}㎡ ≥ 1만㎡ ✓", V.MET
    elif m >= Cfg.MIN_AREA_RELAXED:
        vs, v = f"{m:,.0f}㎡ (5천~1만㎡, 심의 인정 시 완화)", V.MET
    else:
        vs, v = f"{m:,.0f}㎡ < 5천㎡ ✗", V.NOT_MET
    return Req("정비구역 면적", v, value=vs, source_doc=a.면적.source_doc,
               source_span=a.면적.source_span, grade=a.면적.grade, kind="필수")


def _selects(a: Area) -> list[Req]:
    specs = [
        ("과소필지 비율", a.과소필지비율, Cfg.GWASO_RATIO, ">=", "pct", "정비계획 자료(과소필지)"),
        ("주택접도율", a.접도율, Cfg.JEOPDO_MAX, "<=", "pct", "정비계획 자료(주택접도율)"),
        ("호수밀도(ha당)", a.호수밀도, Cfg.HOSU_DENSITY, ">=", "num", "정비계획 자료(호수밀도)"),
        ("노후 연면적 비율", a.노후연면적비율, Cfg.NOHU_AREA_RATIO, ">=", "pct", "정비계획 자료(노후 연면적)"),
        ("반지하 비율", a.반지하비율, Cfg.BANJIHA_RATIO, ">=", "pct",
         "건축물대장 층별개요(지하층 용도) — 표제부엔 없음"),
    ]
    return [_from_fact(n, f, t, o, u, m, "선택") for n, f, t, o, u, m in specs]


@dataclass
class Report:
    reqs: list[Req]
    overall: str
    designated: bool = False
    scope: str = "'될 수 있나'(정비구역 지정 요건) 판정 — 실제 진행/실현 여부는 예측 안 함"
    notes: list[str] = field(default_factory=list)

    @property
    def 요청자료(self) -> list[str]:
        if self.designated:      # 이미 지정 → 지정요건 자료를 더 받을 이유가 없다
            return [r.missing_input for r in self.reqs
                    if r.kind == "참고" and r.verdict == V.INSUFFICIENT and r.missing_input]
        선택충족 = any(r.kind == "선택" and r.verdict == V.MET for r in self.reqs)
        docs = []
        for r in self.reqs:
            if not r.missing_input:
                continue
            if r.verdict != V.INSUFFICIENT and not r.provisional:
                continue
            if r.kind == "선택" and 선택충족:   # 이미 선택 1개 충족 → 나머지 불필요
                continue
            if r.missing_input not in docs:
                docs.append(r.missing_input)
        return docs


_OA = {"가능": "재개발 될 수 있음", "미달": "요건 미달", "확인": "확인 필요",
       "지정": "이미 정비구역으로 지정됨"}


def evaluate(b: Building, a: Area) -> Report:
    if a.지정고시 is not None:
        # 지정 고시가 있으면 지정요건 판정은 끝난 사안.
        # 선택요건(과소필지·접도율·호수밀도·노후연면적)은 지정 심사에서 이미 소진됐으므로
        # 더 물어보지 않는다. 남기는 건 '지정 사실 · 구역 면적 · 내 건물' 셋.
        size = _area_size(a)
        size.kind = "지정"
        reqs = [_designated(a), size, _building_old(b)]
        notes = [f"이미 {a.사업유형} 정비구역으로 지정·고시됨 → 지정요건(노후도·면적·선택요건)은 "
                 f"행정청이 심사해 인정한 상태. 우리가 다시 추정하지 않는다.",
                 "⚠ 이 자료는 현행 고시도형이라 '해제·변경 이력'은 확인할 수 없다. "
                 "구역 존속 여부는 정보몽땅·자치구 고시로 확인 필요.",
                 "⚠ 지정 = 요건 충족(A게이트 통과)일 뿐, 사업이 실제로 진행·완공될지는 예측하지 않는다."]
        return Report(reqs, _OA["지정"], designated=True, notes=notes,
                      scope=f"{a.사업유형} '될 수 있나'(정비구역 지정 요건) 판정"
                            " — 실제 진행/실현 여부는 예측 안 함")

    size = _area_size(a)
    ratio = _area_ratio(a)
    selects = _selects(a)
    building = _building_old(b)
    reqs = [size, ratio, *selects, building]
    notes = []

    if a.노후도_대리지표 and ratio.verdict in (V.MET, V.NOT_MET):
        # 잰 값은 진짜지만 잰 범위가 구역 경계가 아니다 → 확정도 미달도 선언하지 않는다.
        ratio.provisional = True
        ratio.missing_input = "정비구역 경계(정비계획 자료) — 경계 안에서 다시 집계"
        for s_ in selects:
            if s_.name == "노후 연면적 비율" and s_.verdict in (V.MET, V.NOT_MET):
                s_.provisional = True
                s_.missing_input = "정비구역 경계(정비계획 자료) — 경계 안에서 다시 집계"

    def _eff(r):
        """확정 판정용 유효값 — 잠정이면 '확인필요'로 다룬다(반올림 금지)."""
        return V.INSUFFICIENT if r.provisional else r.verdict

    essential = [size, ratio]            # 필수 = 면적 AND 노후도
    if any(_eff(r) == V.NOT_MET for r in essential):
        overall = _OA["미달"]
        notes.append("필수요건(면적·노후도) 미달 → 정비구역 지정 어려움.")
    elif any(_eff(r) in (V.INSUFFICIENT, V.NA) for r in essential):
        overall = _OA["확인"]
        if ratio.provisional:
            notes.append(
                "노후도는 전수 실측이지만 집계 범위가 정비구역 경계가 아니라 대리지표다 "
                "→ 충족/미달을 확정하지 않는다. 경계가 정해지면 그 안에서 다시 세면 확정된다.")
    else:  # 필수 둘 다 MET → 선택요건 1개 이상
        sv = [_eff(s) for s in selects]
        면제 = (a.노후불량비율 is not None and not ratio.provisional
              and a.노후불량비율.value >= Cfg.NOHU_WAIVER)
        if V.MET in sv:
            overall = _OA["가능"]
        elif 면제:
            # 노후 동수가 75% 를 넘으면 서울시가 선택요건을 갖춘 것으로 본다.
            # 조문이 아니라 기관 게시(S1)라 근거를 그대로 밝힌다.
            overall = _OA["가능"]
            notes.append(
                f"선택요건을 값으로 넘긴 건 없지만 노후 동수 "
                f"{a.노후불량비율.value:.0%} ≥ {Cfg.NOHU_WAIVER:.0%} → 선택요건을 갖춘 "
                f"것으로 본다. 근거등급 S1(기관 게시, 조례 조문 아님): "
                f"{Cfg.NOHU_WAIVER_SRC}")
        elif V.INSUFFICIENT in sv:
            overall = _OA["확인"]
            notes.append("필수는 충족. 선택요건(과소필지·접도율·호수밀도·노후연면적·반지하) "
                         "자료 보완 필요.")
        else:
            # 재본 선택요건이 다 미달이어도 '미달'이 아니다 — 영 별표1 제2호의
            # 나머지 목(방재지구·최저고도지구 등)은 우리가 재지 않는다.
            overall = _OA["확인"]
            notes.append(
                "노후도·면적은 충족. 우리가 값으로 재는 선택요건(과소필지·접도율·"
                "호수밀도·노후연면적)은 모두 미달이지만, 영 별표1 제2호는 목이 "
                "가~자 9개다. 안 재는 목이 남아 있어 '미달'로 확정하지 않는다 — "
                + " / ".join(Cfg.SELECT_UNMODELED))
    notes.append(f"※ 기준=서울 조례. 타 지자체는 조례 상이. 출처: {Cfg.SRC}")
    return Report(reqs, overall, notes=notes,
                  scope=f"{a.사업유형} '될 수 있나'(정비구역 지정 요건) 판정"
                        " — 실제 진행/실현 여부는 예측 안 함")


_ICON = {V.MET: "🟢MET", V.NOT_MET: "🔴NOT_MET", V.INSUFFICIENT: "🟡확인필요",
         V.CONFLICT: "🟠CONFLICT", V.NA: "⚪N/A"}


def render(rep: Report) -> str:
    head = {"재개발 될 수 있음": "🟢", "요건 미달": "🔴", "확인 필요": "🟡",
            "이미 정비구역으로 지정됨": "🟢"}
    L = [f"■ 판정: {head.get(rep.overall,'')} {rep.overall}",
         f"  스코프: {rep.scope}", ""]
    sections = ([("필수", "[지정 사실 — 고시로 확정]"), ("지정", "[구역 제원]"),
                 ("참고", "[내 건물 — 참고]")] if rep.designated else
                [("필수", "[필수요건 — 면적·노후도]"), ("선택", "[선택요건 — 1개 이상]"),
                 ("참고", "[내 건물 — 참고]")])
    for kind, title in sections:
        L.append(title)
        for r in rep.reqs:
            if r.kind != kind:
                continue
            L.append(f"  {_ICON[r.verdict]} {r.name}: {r.value or ''}"
                     + ("   〔잠정 — 구역 경계 아님〕" if r.provisional else ""))
            if r.source_span:
                L.append(f"      └ 원문: {r.source_span} [{r.source_doc}]")
            if r.missing_input:
                L.append(f"      └ 필요: {r.missing_input}")
        L.append("")
    if rep.요청자료:
        L.append("── 이 자료를 주시면 판정이 확정됩니다 ──")
        for d in rep.요청자료:
            L.append(f"  📄 {d}")
        L.append("")
    for n in rep.notes:
        L.append(n)
    return "\n".join(L)
