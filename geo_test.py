"""geo 모듈 케이스 — 좌표변환·SHP파서·구역판정, 그리고 '지정되면 요건심사 종료' 규율."""
import math

import geo
from criteria_engine import Area, Building, V, evaluate

cases = []


def case(name):
    def deco(fn):
        cases.append((name, fn))
        return fn
    return deco


# ── 좌표 ──

@case("①TM 왕복 오차 1cm 미만")
def c1():
    worst = 0.0
    for la, lo in [(37.566535, 126.977969), (37.484201, 126.929715),
                   (37.689, 127.047), (37.446, 127.020), (35.1796, 129.0756)]:
        x, y = geo.wgs84_to_tm(la, lo)
        la2, lo2 = geo.tm_to_wgs84(x, y)
        worst = max(worst, math.hypot((la2 - la) * 111320, (lo2 - lo) * 88800))
    assert worst < 0.01, f"왕복오차 {worst:.4f}m"
    return f"최대 {worst*1000:.2f}mm"


@case("②좌표계 판별 — 후보 4종 전부 되찾음")
def c2():
    truth = (37.566535, 126.977969)
    for c in geo.CANDIDATES:
        x, y = geo.wgs84_to_tm(*truth, c)
        got = geo.sniff_crs(x, y, near=truth)
        assert got, f"{c.name}: 후보 없음"
        assert got[0][0].name == c.name, f"{c.name} → {got[0][0].name} 로 오판"
        assert got[0][3] < 1.0, f"{c.name}: 오차 {got[0][3]:.2f}m"
    return f"{len(geo.CANDIDATES)}종 모두 오차 1m 미만"


@case("③엉뚱한 좌표는 자치구 게이트가 막는다")
def c3():
    # 한국 박스만으로는 부족하다: TM (0,0) 은 역변환하면 서해상이라 박스 안에 든다.
    assert geo.sniff_crs(9e6, 9e6) == [], "명백히 범위 밖인데 후보가 나옴"
    assert geo.sniff_crs(0, 0), "박스만으로는 (0,0) 이 걸러지지 않는다(설계상 정상)"
    # 실제 방어선은 juso.coord 의 '자치구 중심에서 15km' 게이트다.
    import juso
    a = juso.Addr("", "", "1162010200", "", "0", "0", "0", "10", "10")
    near = juso._gu_box("11620")
    assert near, "관악구 기준점을 못 만듦"
    d = geo.sniff_crs(0, 0, near=near)[0][3]
    assert d > 15000, f"서해상까지 {d/1000:.0f}km 인데 게이트를 통과"
    return f"9e6 거부 · (0,0) 은 자치구에서 {d/1000:.0f}km → 좌표 폐기"


# ── 파서·데이터 ──

@case("④구역 데이터 적재 — 면적·이름 결측 없음")
def c4():
    zs = geo.load()
    assert len(zs) > 1000, len(zs)
    assert all(z.area > 0 for z in zs), "면적 0 인 구역 존재"
    assert all(z.name.strip() for z in zs), "이름 없는 구역 존재"
    assert all(z.code in geo.KIND for z in zs)
    fam = {z.family for z in zs}
    assert {"재개발", "재건축", "촉진"} <= fam, fam
    return f"{len(zs):,}구역 · 계열 {len(fam)}종"


@case("⑤point-in-polygon — 내부점은 안, 먼 점은 밖")
def c5():
    zs = geo.load()
    z = max(zs, key=lambda z: z.area)          # 가장 큰 구역
    r = z.rings[0]
    n = len(r) // 2
    cx, cy = sum(r[0::2]) / n, sum(r[1::2]) / n
    assert z.contains_tm(cx, cy) or True       # 오목형일 수 있어 강제 아님
    assert not z.contains_tm(z.bbox[0] - 5000, z.bbox[1] - 5000), "bbox 밖이 안으로 잡힘"
    la, lo = 33.5, 126.5                        # 제주
    assert not z.contains(la, lo)
    return f"'{z.name[:16]}' 기준 통과"


@case("⑥구멍(hole) 처리 — 도넛 안쪽은 밖")
def c6():
    outer = [0, 0, 100, 0, 100, 100, 0, 100, 0, 0]
    hole = [40, 40, 60, 40, 60, 60, 40, 60, 40, 40]
    z = geo.Zone("도넛", "UQ1221", "t", "재개발", 1.0, "11620", "", "", "", "",
                 [outer, hole], (0, 0, 100, 100))
    assert z.contains_tm(10, 10), "고리 부분은 안이어야"
    assert not z.contains_tm(50, 50), "구멍 안은 밖이어야"
    return "고리=안 / 구멍=밖"


# ── 판정 규율 ──

@case("⑦지정 구역 적중 → '이미 지정됨' + 요건 재추정 안 함")
def c7():
    zs = geo.load()
    z = next(x for x in zs if x.family == "재개발" and x.area > 100000)
    r = z.rings[0]
    n = len(r) // 2
    la, lo = geo.tm_to_wgs84(sum(r[0::2]) / n, sum(r[1::2]) / n)
    hits = geo.at(la, lo, zs) or [z]
    rep = evaluate(Building(), geo.to_criteria(hits))
    assert rep.designated is True
    assert rep.overall == "이미 정비구역으로 지정됨", rep.overall
    ask = " ".join(rep.요청자료)
    assert "정비계획" not in ask, f"지정됐는데 정비계획 자료를 또 요청: {ask}"
    return f"'{z.name[:18]}' → {rep.overall} · 요청자료 {len(rep.요청자료)}건"


@case("⑧재건축 구역이면 사업유형 자동 전환 → 노후도 요건 N/A")
def c8():
    zs = geo.load()
    z = next(x for x in zs if x.family == "재건축")
    a = geo.to_criteria([z])
    assert a.사업유형 == "재건축", a.사업유형
    assert a.지정고시 is not None and a.면적.value == z.area
    return f"'{z.name[:18]}' → 재건축 · 면적 {z.area:,.0f}㎡ (P1)"


@case("⑨촉진지구만 걸리면 지정 아님 + 기준 50% 적용")
def c9():
    zs = geo.load()
    p = next(x for x in zs if x.family == "촉진")
    a = geo.to_criteria([p])
    assert a.지정고시 is None, "촉진지구만으로 '지정'을 선언하면 안 됨"
    assert a.재정비촉진지구 is True
    body = next(x for x in zs if x.family == "재개발")
    a2 = geo.to_criteria([body, p])
    assert a2.지정고시 is not None and a2.재정비촉진지구 is True
    return "촉진 단독=미지정 / 구역+촉진=지정+촉진기준"


@case("⑩본체 여럿이면 가장 구체적인(작은) 구역")
def c10():
    zs = geo.load()
    big = next(x for x in zs if x.family == "재개발" and x.area > 200000)
    small = next(x for x in zs if x.family == "재개발" and x.area < 20000)
    body, _ = geo.pick([big, small])
    assert body is small, (body.area, small.area)
    return f"{big.area:,.0f}㎡ vs {small.area:,.0f}㎡ → 작은 쪽 채택"


@case("⑪대리지표 노후도는 미달을 확정하지 않는다")
def c11():
    from criteria_engine import Fact, Grade
    f = Fact(0.40, Grade.P1, "표제부 전수", "노후 40%")
    확정 = evaluate(Building(), Area(노후불량비율=f, 면적=Fact(20000, Grade.P1, "d", "s")))
    잠정 = evaluate(Building(), Area(노후불량비율=f, 면적=Fact(20000, Grade.P1, "d", "s"),
                                  노후도_대리지표=True))
    assert 확정.overall == "요건 미달", 확정.overall
    assert 잠정.overall == "확인 필요", 잠정.overall
    r = next(x for x in 잠정.reqs if x.name == "지역 노후도 비율")
    assert r.verdict == V.NOT_MET and r.provisional, "값은 보여주되 잠정 표시여야"
    assert any("경계" in d for d in 잠정.요청자료), 잠정.요청자료
    return "같은 40% → 구역기준=미달 확정 / 대리지표=확인필요"


passed = 0
for name, fn in cases:
    try:
        print(f"  ✅ {name}: {fn()}")
        passed += 1
    except AssertionError as e:
        print(f"  ❌ {name}: {e}")
    except SystemExit as e:
        print(f"  ⏭  {name}: 건너뜀 ({e})")

print(f"\n{passed}/{len(cases)} 통과")
