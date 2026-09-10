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
                연면적=연면적, 대지면적=대지면적, 세대수=0, 가구수=0, 지상층수=2, 부속=부속)


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


# ⑪ 호수밀도 분자는 '세대수'가 아니라 조례 §2⑤ 의 '건축물 동수'다.
#    아파트 한 동에 100세대가 있어도 1동이다. 이 구분을 놓치면 준공된
#    아파트 단지가 초고밀 노후지로 읽힌다(관악 신림1구역: 191호/ha → 12동/ha).
def c11():
    from aging import 동수
    아파트 = B(2020, 지번="1-1")
    아파트.용도, 아파트.세대수, 아파트.지상층수 = "공동주택", 100, 20
    단독 = B(1980, 지번="2-2")
    다가구 = B(1995, 지번="3-3")
    다가구.가구수, 다가구.지상층수 = 8, 4
    상가 = B(1990, 지번="4-4", 연면적=540.0)
    상가.용도, 상가.지상층수 = "제2종근린생활시설", 3

    assert 동수(아파트) == 5, 동수(아파트)      # 가목 — 가장 많은 층의 세대수 (100/20)
    assert 동수(단독) == 1, 동수(단독)
    assert 동수(다가구) == 2, 동수(다가구)      # 가목 — 8가구/4층
    assert 동수(상가) == 2, 동수(상가)          # 바목 — 건축면적 180㎡ / 90㎡
    종전 = sum(max(b.세대수, 0) + max(b.가구수, 0)
              for b in (아파트, 단독, 다가구, 상가))
    새 = sum(동수(b) for b in (아파트, 단독, 다가구, 상가))
    assert 종전 == 108 and 새 == 10, (종전, 새)
    return f"세대+가구 {종전} → 조례 동수 {새} (아파트 100세대=5동)"


case("⑪호수밀도 분자는 세대수가 아니라 조례 §2⑤ 동수", c11)


# ⑫ 주택접도율의 도로 폭은 재개발이면 6m 다 (조례 §6①2나, 2024.5.20 신설).
#    §2⑩ 의 4m 는 주거환경개선구역 몫. 관악 고시문도 개정 전후로 갈린다:
#    2024-04-23 [35193] "폭 4m이상" / 2024-09-23 [36266] "폭 6m이상".
def c12():
    import parcel
    assert parcel.ROAD_W_REDEV == 6.0, parcel.ROAD_W_REDEV
    assert parcel.ROAD_W_GENERAL == 4.0, parcel.ROAD_W_GENERAL
    assert parcel.ROAD_MIN_W == parcel.ROAD_W_REDEV, "기본 판정이 재개발 기준이 아님"
    p = parcel.Parcel("1" * 19, "1-1대", 100.0, 0.0, 0.0, "대", touch=5.0, touch4=5.0)
    assert p.접도 is True and p.접도_일반 is True
    좁은길 = parcel.Parcel("2" * 19, "2-2대", 100.0, 0.0, 0.0, "대", touch=0.0, touch4=5.0)
    assert 좁은길.접도 is False, "6m 기준에서 걸러져야 함"
    assert 좁은길.접도_일반 is True, "4m 기준으로는 접함"
    미계산 = parcel.Parcel("3" * 19, "3-3대", 100.0, 0.0, 0.0, "대")
    assert 미계산.접도 is None and 미계산.접도_일반 is None
    return "재개발 6m / 주거환경개선 4m — 두 값을 따로 보관"


case("⑫재개발 접도율의 도로 폭은 6m (조례 §6①2나)", c12)


# ⑬ 조례 원문이 근거에 그대로 붙는다. 기억이 아니라 인용이어야 한다.
def c13():
    import law
    if not os.path.exists(law.OUT):
        raise SystemExit("law.json 없음 — python law.py --fetch")
    호수 = law.cite("조례", "2", None, 5)
    assert "1헥타르당 건축되어 있는 건축물의 동수" in 호수, 호수[:80]
    과소 = law.cite("조례", "2", None, 9)
    assert 과소.startswith('"과소필지"') and "90제곱미터 미만" in 과소, 과소
    접도 = law.cite("조례", "6", 1, 2, "나")
    assert "도로 폭은 6미터 이상" in 접도, 접도
    assert law.label("조례", "6", 1, 2, "나") == "서울시 도시정비조례 §6①2호나목"
    assert law.label("조례", "2", None, 5) == "서울시 도시정비조례 §2 제5호"
    assert law.cite("조례", "999") == "", "없는 조를 지어내면 안 된다"
    return "§2⑤ · §2⑨ · §6①2나 원문 인용 확인"


case("⑬선택요건 근거는 조례 원문 인용", c13)


# ⑭ 영 별표1 이 없으면 엔진의 근거가 통째로 빈다 — 조문만 받으면 놓친다.
#    선택요건의 목은 가~자 9개이고, 우리가 값으로 재는 건 그중 일부다.
def c14():
    import law, re
    if not os.path.exists(law.OUT):
        raise SystemExit("law.json 없음 — python law.py --fetch")
    a = law.annex("령", "1")
    assert a and "정비계획의 입안대상지역" in a["제목"], a and a["제목"]
    반지하 = law.cite_annex("령", "1", 2, "아")
    assert "지하층" in 반지하 and "2분의 1 이상" in 반지하, 반지하
    방재 = law.cite_annex("령", "1", 2, "사")
    assert "방재지구" in 방재, 방재
    목 = sorted(set(re.findall(r"(?:^|\s)([가-자])\.\s", law.cite_annex("령", "1", 2))))
    assert 목 == list("가나다라마바사아자"), 목
    assert law.label_annex("령", "1", 2, "아") == "시행령 별표1 제2호아목"
    assert law.cite_annex("령", "1", 99) == "", "없는 호를 지어내면 안 된다"
    return f"별표1 제2호 목 {len(목)}개(가~자) · 아목=반지하 · 사목=방재지구"


case("⑭영 별표1 은 조문이 아니라 별표에 있다", c14)


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
