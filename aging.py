"""
aging.py — 건축물대장 표제부 CSV 전수집계 → 지역 노후도 (요건모듈 A 의 [필수] 입력)

criteria_engine 의 Area.노후불량비율 / 노후연면적비율 을 '전수 실측'으로 채운다.
정보몽땅 게시치(S1) 대신 원본 대장 전수(P1) 라서 근거등급이 한 단계 높다.

규율 (프로젝트 공통):
  - 사용승인일 미상은 노후로도 양호로도 반올림하지 않는다 → 비율을 [하한, 상한] 구간으로 낸다.
      하한 = 노후 / 전체            (미상을 전부 '양호' 취급한 최악값)
      상한 = (노후+미상) / 전체     (미상을 전부 '노후' 취급한 최선값)
    하한 ≥ 기준 → MET / 상한 < 기준 → NOT_MET / 걸치면 INSUFFICIENT(확인필요).
  - 경과연수 기준은 조례마다 20~30년. 검증된 값(30년)만 기본, 나머지는 '미검증 변형'으로 병기.
  - 집계 단위가 '정비구역 경계'가 아니라 법정동/도로/지번블록이라는 점을 항상 명시한다.

사용:
  python aging.py                              # 법정동 전체 요약
  python aging.py --by road --top 15           # 도로(길) 단위 노후도 랭킹
  python aging.py --by bun  --top 15           # 본번(지번 블록) 단위
  python aging.py --find 신림로58길              # 이름으로 그 길 찾아 상세
  python aging.py --by road --key 116204160540 --judge   # 그 길 일대로 요건판정
  python aging.py --json data/aging.json       # 집계 결과 저장(웹/서버용)
표준 라이브러리만 사용.
"""

import argparse
import csv
import glob
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

from criteria_engine import Area, Building, Cfg, Fact, Grade, evaluate, render, _BASE

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DOC = "건축물대장 표제부 전수(건축HUB 공개 CSV)"

# ── 경과연수 기준셋 ──
#  '표준30' 만 검증됨(현 Cfg.OLD_YEARS_*). 나머지는 조례 범위 안의 미검증 변형 → 감도용.
RC_KEYS = ("철근콘크리트", "철골", "강구조", "프리캐스트")


def _is_rc(struct: str) -> bool:
    return any(k in (struct or "") for k in RC_KEYS)


def _thr_std(b) -> int:      # 검증됨: 일괄 30년
    return 30


def _thr_loose(b) -> int:    # 미검증: 조례 하한 20년 가정
    return 20


def _thr_mixed(b) -> int:    # 미검증: RC계 30년 / 조적·목조 등 20년
    return 30 if _is_rc(b.구조) else 20


THRESHOLDS = {
    "표준30": (_thr_std, True, "일괄 30년 (도정법 §2·시행령 §2 / 서울 조례 — 검증됨)"),
    "완화20": (_thr_loose, False, "일괄 20년 (조례 범위 하한 가정 — 미검증)"),
    "구조혼합": (_thr_mixed, False, "RC계 30년 / 그 외 20년 (구조별 차등 가정 — 미검증)"),
}


@dataclass
class Bldg:
    """표제부 1행 = 건축물 1동."""
    pk: str
    pnu: str            # 19자리 — 연속지적도 필지와 붙는 조인키
    지번: str
    본번: str
    도로코드: str
    도로명: str
    용도: str
    구조: str
    준공연도: Optional[int]
    연면적: float
    대지면적: float
    세대수: int
    가구수: int
    지상층수: int
    부속: bool

    def 경과연수(self, base: date) -> Optional[int]:
        if self.준공연도 is None:
            return None
        return base.year - self.준공연도


# ── 로딩 ──

def _i(s, d=0):
    s = (s or "").strip()
    try:
        return int(float(s))
    except ValueError:
        return d


def _f(s, d=0.0):
    s = (s or "").strip()
    try:
        return float(s)
    except ValueError:
        return d


def _year(s) -> Optional[int]:
    s = (s or "").strip()
    if len(s) >= 4 and s[:4].isdigit():
        y = int(s[:4])
        if 1900 <= y <= 2100:
            return y
    return None


def _roadname(도로명대지위치: str) -> str:
    """'서울특별시 관악구 신림로58길 62-5 (신림동)' → '신림로58길'."""
    for tok in (도로명대지위치 or "").split():
        if tok.endswith(("로", "길", "대로")) or "로" in tok and tok[-1].isdigit() is False:
            if tok.endswith(("로", "길", "대로")):
                return tok
    return ""


def default_csv() -> Optional[str]:
    hits = sorted(glob.glob(os.path.join(ROOT, "*표제부*.csv")))
    return hits[0] if hits else None


REGION = ""     # load() 가 CSV 첫 행의 '시/구/동'을 여기 채운다 (표시용)


def load(path: Optional[str] = None) -> list[Bldg]:
    path = path or default_csv()
    if not path or not os.path.exists(path):
        raise SystemExit(
            "표제부 CSV 를 못 찾음. 건축HUB(open.eais.go.kr) 에서 '표제부' CSV 를 받아\n"
            f"  {ROOT}/ 에 두거나 --csv 로 경로를 주세요."
        )
    global REGION
    out = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if not REGION:
                REGION = " ".join((r.get("대지위치") or "").split()[:3])
            sgg, bjd = r.get("시군구코드", ""), r.get("법정동코드", "")
            out.append(Bldg(
                pk=r.get("관리건축물대장PK", ""),
                pnu=(f'{sgg}{bjd}1{r.get("번","").zfill(4)[-4:]}{r.get("지","").zfill(4)[-4:]}'
                     if sgg and bjd else ""),
                지번=f'{r.get("번","").lstrip("0") or "0"}-{r.get("지","").lstrip("0") or "0"}',
                본번=r.get("번", "").lstrip("0") or "0",
                도로코드=r.get("새주소도로코드", "").strip(),
                도로명=_roadname(r.get("도로명대지위치", "")),
                용도=r.get("주용도코드명", "").strip(),
                구조=r.get("구조코드명", "").strip(),
                준공연도=_year(r.get("사용승인일")),
                연면적=_f(r.get("연면적(㎡)")),
                대지면적=_f(r.get("대지면적(㎡)")),
                세대수=_i(r.get("세대수(세대)")),
                가구수=_i(r.get("가구수(가구)")),
                지상층수=_i(r.get("지상층수")),
                부속=(r.get("주부속구분코드명", "").strip() == "부속건축물"),
            ))
    return out


# ── 집계 ──

@dataclass
class Jijeok:
    """구역 경계 안 필지 집계 (연속지적도) — 과소필지 요건의 정답 소스."""
    필지: int = 0
    면적합: float = 0.0
    과소_lo: float = 0.0
    과소_hi: float = 0.0
    경계필지: int = 0        # 밴드가 90㎡ 를 걸쳐 확정 못 한 필지
    포착률: float = 0.0      # 필지면적 합 / 고시면적 — 경계 추출 자기점검
    도로필지: int = 0
    접도분모: int = 0        # 접도 판정이 가능한 건물 수
    접도충족: int = 0        # 폭4m 도로에 4m 이상 접한 건물 수


@dataclass
class Aging:
    unit: str            # "법정동" | "도로" | "지번블록" | "정비구역"
    key: str
    label: str
    기준: str
    검증: bool
    total: int = 0
    old: int = 0
    unknown: int = 0
    연면적합: float = 0.0
    노후연면적: float = 0.0
    미상연면적: float = 0.0
    필지수: int = 0
    과소필지: int = 0
    필지면적미상: int = 0
    대지면적합: float = 0.0
    세대수합: int = 0
    by_decade: dict = field(default_factory=dict)
    by_struct: dict = field(default_factory=dict)
    jijeok: Optional["Jijeok"] = None    # 구역 단위 집계일 때만
    zone_area: float = 0.0               # 고시면적 (구역 단위일 때)
    범위밖: bool = False                  # 구역이 가진 표제부 CSV 의 법정동 밖에 있다
    호수: int = 0                        # 세대수 + 가구수 (호수밀도 분자, 정의 미검증)

    @property
    def 접도율(self) -> Optional[float]:
        j = self.jijeok
        if not j or not j.접도분모:
            return None
        return j.접도충족 / j.접도분모

    @property
    def 호수밀도(self) -> Optional[float]:
        """ha 당 호수. 구역 면적을 알아야 계산된다."""
        if not self.zone_area or not self.호수:
            return None
        return self.호수 / (self.zone_area / 10_000)

    # 동수 기준 노후도
    @property
    def lo(self) -> float:
        return self.old / self.total if self.total else 0.0

    @property
    def hi(self) -> float:
        return (self.old + self.unknown) / self.total if self.total else 0.0

    # 연면적 기준 노후도 (선택요건)
    @property
    def area_lo(self) -> float:
        return self.노후연면적 / self.연면적합 if self.연면적합 else 0.0

    @property
    def area_hi(self) -> float:
        return (self.노후연면적 + self.미상연면적) / self.연면적합 if self.연면적합 else 0.0

    def verdict(self, need: float) -> str:
        """구간 대 기준선 → MET / NOT_MET / 확인필요 (반올림 금지)."""
        if self.lo >= need:
            return "MET"
        if self.hi < need:
            return "NOT_MET"
        return "확인필요"


def aggregate(bldgs: list[Bldg], unit: str = "dong", 기준: str = "표준30",
              base: date = _BASE, include_부속: bool = False) -> dict[str, Aging]:
    thr_fn, verified, thr_desc = THRESHOLDS[기준]
    UNIT_NAME = {"dong": "법정동", "road": "도로", "bun": "지번블록"}[unit]

    buckets: dict[str, Aging] = {}
    seen_필지: dict[str, set] = defaultdict(set)   # 과소필지는 '필지' 단위 → 지번 중복 제거

    for b in bldgs:
        if b.부속 and not include_부속:
            continue
        if unit == "dong":
            k, label = "ALL", (REGION or "법정동 전체")
        elif unit == "road":
            k = b.도로코드 or "미상"
            label = b.도로명 or ("도로코드 " + k if k != "미상" else "도로코드 미상")
        else:
            k = b.본번
            label = f"{b.본번}번지 일대"

        ag = buckets.get(k)
        if ag is None:
            ag = buckets[k] = Aging(UNIT_NAME, k, label, 기준, verified)
        if b.도로명 and (not ag.label or ag.label.startswith("도로코드")):
            ag.label = b.도로명

        ag.total += 1
        ag.세대수합 += max(b.세대수, 0)
        yrs = b.경과연수(base)
        need_y = thr_fn(b)
        노후 = None if yrs is None else (yrs >= need_y)

        if 노후 is None:
            ag.unknown += 1
        elif 노후:
            ag.old += 1

        if b.연면적 > 0:
            ag.연면적합 += b.연면적
            if 노후 is None:
                ag.미상연면적 += b.연면적
            elif 노후:
                ag.노후연면적 += b.연면적

        # 필지(지번) 단위 — 같은 지번에 여러 동이 서면 1필지로만 셈
        if b.지번 not in seen_필지[k]:
            seen_필지[k].add(b.지번)
            ag.필지수 += 1
            if b.대지면적 > 0:
                ag.대지면적합 += b.대지면적
                if b.대지면적 < 90:
                    ag.과소필지 += 1
            else:
                ag.필지면적미상 += 1

        dec = "미상" if b.준공연도 is None else f"{b.준공연도 // 10 * 10}s"
        ag.by_decade[dec] = ag.by_decade.get(dec, 0) + 1
        st = b.구조 or "미상"
        ag.by_struct[st] = ag.by_struct.get(st, 0) + 1

    return buckets


def aggregate_zone(bldgs: list[Bldg], zone, parcels=None, 기준: str = "표준30",
                   base: date = _BASE, include_부속: bool = False) -> Aging:
    """정비구역 경계 '안'의 건물만 집계.

    여기서 나온 노후도는 대리지표가 아니라 **요건이 말하는 그 값**이다(집계 범위가 곧 구역).
    포함 판정은 연속지적도 필지 중심점 기준 → 그 필지의 PNU 를 가진 대장 건물만 센다.
    """
    import parcel as PARCEL
    ps = parcels if parcels is not None else PARCEL.load(
        zone.sigungu if zone.sigungu != "11000" else None)
    hits = PARCEL.in_zone(zone, ps)
    pnus = {p.pnu for p in hits}

    # 구역이 CSV 범위(담고 있는 법정동) 밖이면 '건물 0동' 은 철거가 아니라 자료 없음이다.
    have = {b.pnu[:10] for b in bldgs if b.pnu}
    zone_bjd = {p.pnu[:10] for p in hits}
    밖 = bool(zone_bjd) and not (zone_bjd & have)

    thr_fn, verified, _ = THRESHOLDS[기준]
    ag = Aging("정비구역", zone.name, zone.name, 기준, verified)
    ag.zone_area = zone.area
    ag.범위밖 = 밖
    lo, hi, edge = PARCEL.gwaso_ratio(hits)
    ag.jijeok = Jijeok(len(hits), sum(p.area for p in hits), lo, hi, edge,
                       PARCEL.coverage(zone, hits),
                       도로필지=sum(1 for p in hits if p.도로))
    touch = {p.pnu: p.접도 for p in hits}

    seen = set()
    for b in bldgs:
        if b.pnu not in pnus:
            continue
        if b.부속 and not include_부속:
            continue
        ag.total += 1
        ag.세대수합 += max(b.세대수, 0)
        ag.호수 += max(b.세대수, 0) + max(b.가구수, 0)
        t = touch.get(b.pnu)
        if t is not None:
            ag.jijeok.접도분모 += 1
            ag.jijeok.접도충족 += bool(t)
        yrs = b.경과연수(base)
        노후 = None if yrs is None else (yrs >= thr_fn(b))
        if 노후 is None:
            ag.unknown += 1
        elif 노후:
            ag.old += 1
        if b.연면적 > 0:
            ag.연면적합 += b.연면적
            if 노후 is None:
                ag.미상연면적 += b.연면적
            elif 노후:
                ag.노후연면적 += b.연면적
        if b.지번 not in seen:
            seen.add(b.지번)
            ag.필지수 += 1
        dec = "미상" if b.준공연도 is None else f"{b.준공연도 // 10 * 10}s"
        ag.by_decade[dec] = ag.by_decade.get(dec, 0) + 1
        st = b.구조 or "미상"
        ag.by_struct[st] = ag.by_struct.get(st, 0) + 1
    return ag


def phase_signal(ag: Aging) -> Optional[tuple[str, str, str]]:
    """구역 안 건물 구성에서 읽히는 **사업 단계 신호**. (아이콘, 라벨, 근거)

    ⚠ 이건 추정이지 확정이 아니다. 진행단계(조합설립·사업시행인가·착공)는
       오픈데이터 배포가 없어서 정보몽땅·자치구 고시로 확인해야 한다.
       여기서 하는 건 '지금 그 땅 위에 뭐가 서 있는지'를 보고 읽는 것뿐이다.
    """
    if ag.unit != "정비구역":
        return None
    if ag.범위밖:
        return None          # 자료가 없는 것을 '철거' 로 읽지 않는다
    j = ag.jijeok
    필지 = j.필지 if j else 0
    if 필지 < 5:
        return None
    if ag.total == 0:
        return ("🏗", "철거 완료 추정 — 공사 중",
                f"구역 안 필지 {필지:,}개인데 유효한 대장 건물 0동")
    if ag.hi <= 0.20:
        per = ag.세대수합 / 필지 if 필지 else 0
        return ("🏢", "준공 완료 추정 — 이미 새 집",
                f"건물 {ag.total}동 전부 신축(노후 {ag.lo:.0%}) · "
                f"필지 {필지}개에 {ag.세대수합:,}세대({per:.0f}세대/필지)")
    if ag.lo >= Cfg.REDEV_RATIO:
        return ("🧱", "미착공 추정 — 옛 건물 그대로",
                f"건물 {ag.total:,}동 중 노후 {ag.lo:.0%} · 필지 {필지:,}개")
    return ("🔀", "혼재 — 단계 확인 필요",
            f"노후 {ag.lo:.0%} (준공도 미착공도 아닌 구성)")


# 사업 단계 신호(대장 실측 추정) ↔ 정보몽땅 게시 단계 — 두 독립 소스 교차검증
_PHASE_EXPECT = {
    "미착공 추정 — 옛 건물 그대로": ("정비계획 수립", "정비구역지정", "추진위구성",
                          "추진위원회승인", "조합규약작성", "조합창립총회",
                          "조합설립인가", "사업시행인가", "안전진단",
                          "지구단위계획수립/건축심의/교통심의"),
    "철거 완료 추정 — 공사 중": ("관리처분인가", "철거", "철거 및 착공", "착공", "분양"),
    "준공 완료 추정 — 이미 새 집": ("준공인가", "사용검수 및 입주", "이전고시",
                            "조합해산", "청산 및 조합해산", "조합청산", "분양"),
}


def cross_check(ag: Aging, site) -> Optional[tuple[bool, str]]:
    """(일치여부, 설명). 추정과 게시가 어긋나면 그 사실을 드러낸다 — 숨기지 않는다."""
    ph = phase_signal(ag)
    if not ph or site is None or not site.stage:
        return None
    expect = _PHASE_EXPECT.get(ph[1])
    if expect is None:
        return None
    ok = site.stage in expect
    if ok:
        return (True, f"대장 실측 추정 '{ph[1]}' vs 정보몽땅 게시 '{site.stage}'  → 일치")
    return (False,
            f"대장 실측 추정 '{ph[1]}' vs 정보몽땅 게시 '{site.stage}'  → 어긋남. "
            "고시도형에는 같은 땅의 **옛 구역과 현행 구역이 함께** 실려 있어, "
            "이미 끝난 세대의 구역을 현행 사업장에 붙였을 수 있다(게시 갱신 시차도 원인).")


# ── criteria_engine 연결 ──

def to_facts(ag: Aging) -> dict:
    """집계 → Fact. 구간이 확정될 때만 Fact 를 주고, 걸치면 None(=확인필요)."""
    out = {"노후불량비율": None, "노후연면적비율": None, "과소필지비율": None,
           "접도율": None}
    span = (f"{ag.label} 주건축물 {ag.total}동 중 노후 {ag.old}동"
            f"{f' · 준공일 미상 {ag.unknown}동' if ag.unknown else ''} "
            f"(경과연수 기준 {ag.기준})")
    if ag.unknown == 0 or ag.verdict(Cfg.REDEV_RATIO) != "확인필요":
        # 구간이 기준선을 걸치지 않으면 하한값으로 판정해도 결론이 안 바뀜
        out["노후불량비율"] = Fact(ag.lo, Grade.P1, SRC_DOC, span)
    if ag.연면적합 > 0 and (ag.미상연면적 == 0
                          or (ag.area_lo >= Cfg.NOHU_AREA_RATIO)
                          or (ag.area_hi < Cfg.NOHU_AREA_RATIO)):
        out["노후연면적비율"] = Fact(
            ag.area_lo, Grade.P1, SRC_DOC,
            f"{ag.label} 연면적 {ag.연면적합:,.0f}㎡ 중 노후 {ag.노후연면적:,.0f}㎡")
    # 과소필지 — 연속지적도 필지 단위(요건이 말하는 그 단위). 밴드가 기준을 걸치면 발급하지 않는다.
    j = ag.jijeok
    if j and j.필지:
        import parcel as PARCEL
        need = Cfg.GWASO_RATIO
        확정 = (j.경계필지 == 0) or (j.과소_lo >= need) or (j.과소_hi < need)
        if 확정:
            out["과소필지비율"] = Fact(
                j.과소_lo, Grade.P1, PARCEL.SRC_DOC,
                f"{ag.label} 필지 {j.필지:,}개 중 90㎡ 미만 {round(j.과소_lo*j.필지)}개"
                + (f" (밴드 경계 {j.경계필지}개 제외)" if j.경계필지 else "")
                + f" · 필지면적 합 {j.면적합:,.0f}㎡ = 고시면적의 {j.포착률:.0%}")
    # 주택접도율 — 지목 '도' 기준 근사. 현황도로 미반영이라 실제보다 낮게 나올 수 있고,
    # 낮을수록 요건에 '유리'하므로 기준선 근처에서는 발급하지 않는다(유리한 쪽 반올림 금지).
    r = ag.접도율
    if r is not None and j and j.접도분모 >= 10:
        need = Cfg.JEOPDO_MAX
        if abs(r - need) > 0.05:      # 기준선 ±5%p 밖일 때만 확정
            out["접도율"] = Fact(
                r, Grade.P1, PARCEL.SRC_DOC,
                f"{ag.label} 건물 {j.접도분모:,}동 중 폭 {PARCEL.ROAD_MIN_W:.0f}m 도로에 "
                f"{PARCEL.TOUCH_MIN:.0f}m 이상 접한 것 {j.접도충족:,}동 "
                f"(구역 안 도로필지 {j.도로필지}) · {PARCEL.ROAD_NOTE}")
    return out


def to_area(ag: Aging, 면적: Optional[float] = None, 촉진: bool = False,
            proxy: bool = True) -> Area:
    """집계 → Area.

    proxy=True(기본): 집계 단위(법정동·도로·지번블록)가 정비구역 경계가 아니므로
      노후도로 충족/미달을 '확정'하지 않는다. 값과 판정은 그대로 보여주되 잠정 표시.
    proxy=False: 사용자가 '이 범위를 구역으로 본다'고 명시한 경우(aging.py --judge).
    """
    f = to_facts(ag)
    if ag.unit == "정비구역":
        # 집계 범위가 곧 구역 경계 → 대리지표가 아니라 요건 그 자체. 면적도 고시치를 쓴다.
        proxy = False
        if 면적 is None and ag.zone_area:
            면적 = ag.zone_area
    return Area(
        사업유형="재개발", 지역="서울", 재정비촉진지구=촉진,
        면적=(Fact(면적, Grade.P1 if ag.unit == "정비구역" else Grade.T,
                  "고시도형" if ag.unit == "정비구역" else "사용자 입력",
                  f"구역 면적 {면적:,.0f}㎡") if 면적 else None),
        노후불량비율=f["노후불량비율"],
        노후연면적비율=f["노후연면적비율"],
        과소필지비율=f["과소필지비율"],
        접도율=f["접도율"],
        노후도_대리지표=proxy,
    )


# ── 출력 ──

def _bar(v: float, w: int = 20) -> str:
    n = max(0, min(w, round(v * w)))
    return "█" * n + "░" * (w - n)


def render_aging(ag: Aging, detail: bool = True) -> str:
    need = Cfg.REDEV_RATIO
    v = ag.verdict(need)
    icon = {"MET": "🟢", "NOT_MET": "🔴", "확인필요": "🟡"}[v]
    L = [f"■ {ag.unit} · {ag.label}  (주건축물 {ag.total:,}동)",
         f"  경과연수 기준: {ag.기준} — {THRESHOLDS[ag.기준][2]}"]
    if not ag.검증:
        L.append("  ⚠ 이 기준값은 미검증 변형(감도 확인용). 판정 근거로 쓰지 말 것.")
    rng = f"{ag.lo:.1%}" if ag.unknown == 0 else f"{ag.lo:.1%} ~ {ag.hi:.1%}"
    L += ["",
          f"  {icon} 노후·불량 동수 비율  {rng}   (기준 {need:.0%})",
          f"      {_bar(ag.lo)}  노후 {ag.old:,}동 / 전체 {ag.total:,}동"
          + (f" / 준공일 미상 {ag.unknown:,}동" if ag.unknown else "")]
    if ag.연면적합 > 0:
        arng = f"{ag.area_lo:.1%}" if ag.미상연면적 == 0 else f"{ag.area_lo:.1%} ~ {ag.area_hi:.1%}"
        L.append(f"  · 노후 연면적 비율   {arng}   (선택요건 기준 {Cfg.NOHU_AREA_RATIO:.0%})")
    if ag.jijeok and ag.jijeok.필지:
        j = ag.jijeok
        rng = f"{j.과소_lo:.1%}" if j.경계필지 == 0 else f"{j.과소_lo:.1%} ~ {j.과소_hi:.1%}"
        L.append(f"  · 과소필지(90㎡미만) {rng}   (선택요건 기준 {Cfg.GWASO_RATIO:.0%})"
                 f"  [연속지적 {j.필지:,}필지"
                 + (f", 밴드 경계 {j.경계필지}개" if j.경계필지 else "") + "]")
        L.append(f"  · 필지면적 합 {j.면적합:,.0f}㎡ / 고시면적 {ag.zone_area:,.0f}㎡"
                 f" = 포착률 {j.포착률:.1%}  ← 경계 추출 자기점검")
        if ag.접도율 is not None:
            L.append(f"  · 주택접도율 {ag.접도율:.1%}   (선택요건 기준 {Cfg.JEOPDO_MAX:.0%} 이하)"
                     f"  [{j.접도충족:,}/{j.접도분모:,}동, 구역 안 도로 {j.도로필지}필지]")
            L.append(f"      └ 지목 '도' 기준 근사 — 현황도로(사도·통행로) 미반영")
        if ag.호수밀도 is not None:
            L.append(f"  · 호수밀도 {ag.호수밀도:,.0f}호/ha   (선택요건 기준 {Cfg.HOSU_DENSITY}호 이상)"
                     f"  [{ag.호수:,}호 / {ag.zone_area/10000:.2f}ha]  — 정의 미검증, 참고치")
    elif ag.필지수:
        known = ag.필지수 - ag.필지면적미상
        if known:
            g_lo = ag.과소필지 / ag.필지수
            g_hi = (ag.과소필지 + ag.필지면적미상) / ag.필지수
            L.append(f"  · 과소필지(90㎡미만) {g_lo:.1%} ~ {g_hi:.1%} "
                     f"[{ag.과소필지}/{ag.필지수}필지, 대지면적 미상 {ag.필지면적미상}] "
                     f"— 잠정·확인필요")
    if detail:
        L += ["", "  [준공 연대]"]
        for k in sorted(ag.by_decade, key=lambda x: (x == "미상", x)):
            n = ag.by_decade[k]
            L.append(f"    {k:>6} {_bar(n / ag.total, 24)} {n:,}")
        L += ["", "  [구조]"]
        for k, n in sorted(ag.by_struct.items(), key=lambda kv: -kv[1])[:6]:
            L.append(f"    {k:<12} {n:,} ({n / ag.total:.0%})")
    ph = phase_signal(ag)
    if ph:
        L += ["", f"  {ph[0]} 사업 단계 신호: {ph[1]}",
              f"      근거: {ph[2]}",
              "      ⚠ 추정입니다 — 조합설립·사업시행인가 등 실제 단계는 정보몽땅·자치구 고시로 확인."]
    if ag.범위밖:
        L += ["", "  ⚪ 이 구역은 가진 표제부 CSV 의 법정동 밖입니다 — 건물 수치는 '없음' 이",
              "     아니라 '자료 없음' 입니다. 그 동의 표제부 CSV 를 받아야 집계됩니다."]
    if ag.unit == "정비구역":
        L += ["",
              "  ✅ 집계 범위가 곧 고시된 정비구역 경계입니다 — 대리지표가 아니라 요건 그 값.",
              "     (포함 판정은 연속지적도 필지 중심점 기준)",
              f"  출처: {SRC_DOC} + 연속지적도 + 고시도형"]
    else:
        L += ["",
              "  ⚠ 집계 단위는 '정비구역 경계'가 아니라 " + ag.unit + " 입니다.",
              "     실제 구역 노후도는 지정된 경계 안에서 다시 세야 하며, 이 값은 그 대리지표입니다.",
              f"  출처: {SRC_DOC}"]
    return "\n".join(L)


def render_rank(buckets: dict[str, Aging], top: int, min_total: int) -> str:
    need = Cfg.REDEV_RATIO
    items = [a for a in buckets.values() if a.total >= min_total and a.key != "미상"]
    items.sort(key=lambda a: (-a.lo, -a.total))
    L = [f"■ 노후도 랭킹 (동수 {min_total}동 이상 · 상위 {top})  기준 {need:.0%}",
         f"{'':2}{'단위':<16}{'동수':>6}{'노후율':>16}  {'':22}"]
    for i, a in enumerate(items[:top], 1):
        v = a.verdict(need)
        icon = {"MET": "🟢", "NOT_MET": "🔴", "확인필요": "🟡"}[v]
        rng = f"{a.lo:.0%}" if a.unknown == 0 else f"{a.lo:.0%}~{a.hi:.0%}"
        L.append(f"{i:2} {a.label[:15]:<16}{a.total:>6,}{rng:>16}  {_bar(a.lo)} {icon}")
    tail = len(items) - top
    if tail > 0:
        L.append(f"   … 외 {tail}개 (동수 {min_total} 미만 {len(buckets) - len(items)}개는 제외)")
    return "\n".join(L)


def summarize(buckets: dict[str, Aging]) -> dict:
    """웹/서버용 직렬화."""
    out = []
    for a in buckets.values():
        d = asdict(a)
        d.update(lo=a.lo, hi=a.hi, area_lo=a.area_lo, area_hi=a.area_hi,
                 verdict=a.verdict(Cfg.REDEV_RATIO))
        out.append(d)
    out.sort(key=lambda d: -d["total"])
    return {"기준선": Cfg.REDEV_RATIO, "출처": SRC_DOC,
            "기준일": _BASE.isoformat(), "buckets": out}


# ── CLI ──

def main(argv=None):
    p = argparse.ArgumentParser(description="표제부 CSV 전수집계 → 지역 노후도")
    p.add_argument("--csv", help="표제부 CSV 경로 (기본: 프로젝트 안 *표제부*.csv)")
    p.add_argument("--by", choices=["dong", "road", "bun"], default="dong")
    p.add_argument("--기준", "--thr", dest="기준", choices=list(THRESHOLDS), default="표준30")
    p.add_argument("--key", help="집계 단위 키 (도로코드 / 본번)")
    p.add_argument("--find", help="이름으로 찾기 (예: 신림로58길)")
    p.add_argument("--zone", help="정비구역 경계로 집계 (예: 신림7) — 대리지표가 아닌 실측")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-total", type=int, default=30, help="랭킹에 넣을 최소 동수")
    p.add_argument("--judge", action="store_true", help="요건모듈(A)로 판정까지")
    p.add_argument("--area", type=float, help="구역 면적(㎡) — 알면 입력, 판정 확정용")
    p.add_argument("--촉진", action="store_true", help="재정비촉진지구(기준 50%%)")
    p.add_argument("--json", help="집계 결과를 JSON 으로 저장")
    p.add_argument("--sens", action="store_true", help="기준 3종 감도 비교")
    a = p.parse_args(argv)

    bldgs = load(a.csv)

    if a.zone:
        import geo
        zs = geo.search(a.zone)
        if not zs:
            raise SystemExit(f"'{a.zone}' 구역 없음. python geo.py --search 로 찾아보세요.")
        z = zs[0]
        ag = aggregate_zone(bldgs, z, 기준=a.기준)
        print(render_aging(ag))
        if a.judge:
            from criteria_engine import Building, evaluate, render as crender
            print("\n" + "=" * 60)
            print(crender(evaluate(Building(), to_area(ag, 촉진=a.촉진))))
        return

    buckets = aggregate(bldgs, a.by, a.기준)

    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)) or ".", exist_ok=True)
        payload = {"기준선": Cfg.REDEV_RATIO, "출처": SRC_DOC,
                   "기준일": _BASE.isoformat(), "기준": a.기준,
                   "지역": REGION, "units": {}}
        for u in ("dong", "road", "bun"):
            payload["units"][u] = summarize(aggregate(bldgs, u, a.기준))["buckets"]
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        n = {u: len(v) for u, v in payload["units"].items()}
        print(f"저장: {a.json}  {n}")
        return

    target = None
    if a.key:
        target = buckets.get(a.key)
        if target is None:
            raise SystemExit(f"키 {a.key} 없음. --top 으로 목록을 먼저 보세요.")
    elif a.find:
        hits = [x for x in buckets.values() if a.find in x.label]
        if not hits:
            raise SystemExit(f"'{a.find}' 매칭 없음.")
        target = max(hits, key=lambda x: x.total)
    elif a.by == "dong":
        target = buckets["ALL"]

    if target is None:
        print(render_rank(buckets, a.top, a.min_total))
        return

    print(render_aging(target))

    if a.sens:
        print("\n  [경과연수 기준 감도]")
        for name in THRESHOLDS:
            bb = aggregate(bldgs, a.by, name)
            t = bb.get(target.key)
            if not t:
                continue
            mark = "✔검증" if THRESHOLDS[name][1] else "미검증"
            rng = f"{t.lo:.1%}" if t.unknown == 0 else f"{t.lo:.1%}~{t.hi:.1%}"
            print(f"    {name:<6}({mark}) 노후율 {rng:>14}  {_bar(t.lo)}")

    if a.judge:
        print("\n" + "=" * 60)
        # --judge 는 사용자가 이 집계 범위를 구역으로 간주한 것 → 확정 판정
        area = to_area(target, a.area, a.촉진, proxy=False)
        b = Building()
        print(render(evaluate(b, area)))


if __name__ == "__main__":
    main()
