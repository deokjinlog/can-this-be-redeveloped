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
            out.append(Bldg(
                pk=r.get("관리건축물대장PK", ""),
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
                지상층수=_i(r.get("지상층수")),
                부속=(r.get("주부속구분코드명", "").strip() == "부속건축물"),
            ))
    return out


# ── 집계 ──

@dataclass
class Aging:
    unit: str            # "법정동" | "도로" | "지번블록"
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


# ── criteria_engine 연결 ──

def to_facts(ag: Aging) -> dict:
    """집계 → Fact. 구간이 확정될 때만 Fact 를 주고, 걸치면 None(=확인필요)."""
    out = {"노후불량비율": None, "노후연면적비율": None}
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
    return out


def to_area(ag: Aging, 면적: Optional[float] = None, 촉진: bool = False) -> Area:
    f = to_facts(ag)
    return Area(
        사업유형="재개발", 지역="서울", 재정비촉진지구=촉진,
        면적=(Fact(면적, Grade.T, "사용자 입력", f"구역 면적 {면적:,.0f}㎡") if 면적 else None),
        노후불량비율=f["노후불량비율"],
        노후연면적비율=f["노후연면적비율"],
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
    if ag.필지수:
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
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-total", type=int, default=30, help="랭킹에 넣을 최소 동수")
    p.add_argument("--judge", action="store_true", help="요건모듈(A)로 판정까지")
    p.add_argument("--area", type=float, help="구역 면적(㎡) — 알면 입력, 판정 확정용")
    p.add_argument("--촉진", action="store_true", help="재정비촉진지구(기준 50%%)")
    p.add_argument("--json", help="집계 결과를 JSON 으로 저장")
    p.add_argument("--sens", action="store_true", help="기준 3종 감도 비교")
    a = p.parse_args(argv)

    bldgs = load(a.csv)
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
        area = to_area(target, a.area, a.촉진)
        b = Building()
        print(render(evaluate(b, area)))


if __name__ == "__main__":
    main()
