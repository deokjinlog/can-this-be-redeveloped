"""aging 모듈 케이스 — 전수집계 노후도의 '반올림 금지' 규율 검증."""
import os
from datetime import date

from aging import (Aging, Bldg, THRESHOLDS, aggregate, default_csv, load,
                   to_facts, render_aging)
from criteria_engine import Cfg, Grade

BASE = date(2026, 9, 1)


def B(연도, 구조="철근콘크리트구조", 지번="1-1", 연면적=100.0, 대지면적=200.0,
      부속=False, 도로="R1", 본번="1", pnu=""):
    return Bldg(pk=f"{지번}/{연도}/{id(구조)}",
                pnu=pnu or f"11620102001{본번.zfill(4)}{지번.split('-')[-1].zfill(4)}",
                지번=지번, 본번=본번, 도로코드=도로,
                도로명="테스트길", 용도="단독주택", 구조=구조, 준공연도=연도,
                연면적=연면적, 대지면적=대지면적, 세대수=0, 지상층수=2, 부속=부속)


def one(bldgs, unit="dong", 기준="표준30"):
    return aggregate(bldgs, unit, 기준, base=BASE)["ALL" if unit == "dong" else "R1"]


cases = []


def case(name, fn):
    cases.append((name, fn))


# ① 미상은 노후로도 양호로도 반올림하지 않는다 → 구간이 벌어진다
def c1():
    ag = one([B(1990)] * 6 + [B(2020)] * 2 + [B(None)] * 2)
    assert ag.total == 10 and ag.old == 6 and ag.unknown == 2, (ag.total, ag.old, ag.unknown)
    assert abs(ag.lo - 0.60) < 1e-9 and abs(ag.hi - 0.80) < 1e-9, (ag.lo, ag.hi)
    return f"노후율 {ag.lo:.0%}~{ag.hi:.0%} (미상 {ag.unknown}동 반올림 안 함)"


case("①미상은 구간으로", c1)


# ② 구간이 기준선을 걸치면 확인필요 — MET 로 올려치지 않는다
def c2():
    ag = one([B(1990)] * 5 + [B(2020)] * 4 + [B(None)])   # lo 50% hi 60%
    assert ag.verdict(Cfg.REDEV_RATIO) == "확인필요", ag.verdict(Cfg.REDEV_RATIO)
    assert to_facts(ag)["노후불량비율"] is None, "걸치면 Fact 를 주면 안 됨"
    return f"lo {ag.lo:.0%} / hi {ag.hi:.0%} → 확인필요, Fact 미발급"


case("②걸치면 확인필요", c2)


# ③ 상한조차 기준 미만이면 NOT_MET 으로 확정 (미상이 있어도 결론 불변)
def c3():
    ag = one([B(1990)] * 3 + [B(2020)] * 6 + [B(None)])   # lo 30% hi 40%
    assert ag.verdict(Cfg.REDEV_RATIO) == "NOT_MET"
    f = to_facts(ag)["노후불량비율"]
    assert f is not None and f.grade is Grade.P1
    return f"lo {ag.lo:.0%} / hi {ag.hi:.0%} → NOT_MET 확정 (P1)"


case("③상한<기준이면 확정", c3)


# ④ 하한이 이미 기준 이상이면 MET 확정
def c4():
    ag = one([B(1990)] * 7 + [B(2020)] * 2 + [B(None)])   # lo 70%
    assert ag.verdict(Cfg.REDEV_RATIO) == "MET"
    assert to_facts(ag)["노후불량비율"].value >= Cfg.REDEV_RATIO
    return f"lo {ag.lo:.0%} ≥ 60% → MET 확정"


case("④하한≥기준이면 확정", c4)


# ⑤ 부속건축물은 동수에서 제외 (기본)
def c5():
    bs = [B(1990)] * 3 + [B(2020, 부속=True)] * 5
    ag = one(bs)
    assert ag.total == 3, ag.total
    inc = aggregate(bs, "dong", "표준30", base=BASE, include_부속=True)["ALL"]
    assert inc.total == 8
    return f"주건축물만 {ag.total}동 (부속 포함 시 {inc.total}동)"


case("⑤부속건축물 제외", c5)


# ⑥ 같은 지번에 여러 동이 서도 '필지'는 1개 (과소필지 분모 중복 방지)
def c6():
    ag = one([B(1990, 지번="7-7", 대지면적=80.0) for _ in range(4)] +
             [B(1990, 지번="8-8", 대지면적=300.0)])
    assert ag.total == 5 and ag.필지수 == 2, (ag.total, ag.필지수)
    assert ag.과소필지 == 1, ag.과소필지
    return f"{ag.total}동 / {ag.필지수}필지 · 과소 {ag.과소필지}"


case("⑥필지는 지번 단위", c6)


# ⑦ 대지면적 0(미상)은 과소필지 판정에서 빠지고 '미상'으로 남는다
def c7():
    ag = one([B(1990, 지번="1-1", 대지면적=0.0), B(1990, 지번="2-2", 대지면적=50.0)])
    assert ag.필지면적미상 == 1 and ag.과소필지 == 1
    return f"과소 {ag.과소필지} / 면적미상 {ag.필지면적미상} → 잠정만"


case("⑦대지면적 미상 분리", c7)


# ⑧ 연면적 노후비율은 연면적>0 만 분모에 넣는다
def c8():
    ag = one([B(1990, 연면적=900.0), B(2020, 연면적=100.0), B(2020, 연면적=0.0)])
    assert abs(ag.연면적합 - 1000.0) < 1e-9, ag.연면적합
    assert abs(ag.area_lo - 0.9) < 1e-9
    return f"연면적 {ag.연면적합:,.0f}㎡ 중 노후 {ag.area_lo:.0%}"


case("⑧연면적 분모는 실측만", c8)


# ⑨ 기준셋: 구조혼합에서 조적조 25년은 노후, RC 25년은 양호
def c9():
    bs = [B(2001, "벽돌구조"), B(2001, "철근콘크리트구조")]
    std = one(bs, 기준="표준30")
    mix = one(bs, 기준="구조혼합")
    assert std.old == 0 and mix.old == 1, (std.old, mix.old)
    assert THRESHOLDS["표준30"][1] is True and THRESHOLDS["구조혼합"][1] is False
    return f"표준30 노후 {std.old}동 / 구조혼합 노후 {mix.old}동 (미검증 표시됨)"


case("⑨구조별 기준 감도", c9)


# ⑩ 집계 단위 분리 (도로 vs 지번블록)
def c10():
    bs = [B(1990, 도로="R1", 본번="10"), B(2020, 도로="R2", 본번="10"),
          B(1990, 도로="R2", 본번="20")]
    road = aggregate(bs, "road", "표준30", base=BASE)
    bun = aggregate(bs, "bun", "표준30", base=BASE)
    assert set(road) == {"R1", "R2"} and road["R2"].total == 2
    assert set(bun) == {"10", "20"} and bun["10"].total == 2
    return f"도로 {len(road)}개 / 지번블록 {len(bun)}개"


case("⑩집계 단위 분리", c10)


passed = 0
for name, fn in cases:
    try:
        msg = fn()
        passed += 1
        print(f"  ✅ {name}: {msg}")
    except AssertionError as e:
        print(f"  ❌ {name}: {e}")

print(f"\n{passed}/{len(cases)} 통과\n")

# ── 실 CSV 스모크 (있을 때만) ──
csv_path = default_csv()
if csv_path:
    bl = load(csv_path)
    ag = aggregate(bl, "dong", "표준30")["ALL"]
    print("=" * 60)
    print(f"실 CSV 스모크 — {os.path.basename(csv_path)} ({len(bl):,}행)")
    print("=" * 60)
    print(render_aging(ag, detail=False))
else:
    print("(표제부 CSV 없음 — 스모크 생략)")
