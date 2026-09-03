"""필지·주소 케이스 — 구역 경계 안 실측이 대리지표를 대체하는지, 밴드가 정직한지."""
import geo
import parcel
from criteria_engine import Building, Cfg, V, evaluate

cases = []


def case(name):
    def deco(fn):
        cases.append((name, fn))
        return fn
    return deco


@case("①PNU 조립 규칙")
def c1():
    assert parcel.pnu_of("11620", "10200", "10", "10") == "1162010200100100010"
    assert parcel.pnu_of("11620", "10200", "0675", "0159") == "1162010200106750159"
    assert parcel.pnu_of("11620", "10200", "105", "39", san=True) == "1162010200201050039"
    return "지번/산 구분 · 4자리 패딩"


@case("②필지 적재")
def c2():
    ps = parcel.load("11620")
    assert len(ps) > 40000, len(ps)
    assert all(len(k) == 19 for k in list(ps)[:100])
    areas = [p.area for p in list(ps.values())[:3000]]
    assert min(areas) >= 0 and max(areas) < 1e7
    return f"{len(ps):,}필지"


@case("③면적 밴드가 90㎡ 를 걸치면 확정하지 않는다")
def c3():
    lo, hi = parcel.BAND_LO, parcel.BAND_HI
    mk = lambda a: parcel.Parcel("x", "y", a, 0, 0)
    assert mk(80).과소 == "MET", mk(80).band          # 80*1.042=83 < 90
    assert mk(120).과소 == "NOT_MET", mk(120).band     # 120*0.897=108 >= 90
    edge = mk(90)
    assert edge.과소 == "확인필요", edge.band
    assert lo < 1 < hi
    return f"밴드 ×{lo:.3f}~{hi:.3f} · 90㎡ 근처는 확인필요"


@case("④구역 경계 추출 자기점검 — 포착률 90% 이상")
def c4():
    ps = parcel.load("11620")
    zs = [z for z in geo.load() if z.sigungu in ("11620", "11000")]
    tested = ok = 0
    worst = None
    for z in zs:
        hits = parcel.in_zone(z, ps)
        if len(hits) < 5:
            continue
        cov = parcel.coverage(z, hits)
        tested += 1
        if 0.90 <= cov <= 1.10:
            ok += 1
        elif worst is None or abs(cov - 1) > abs(worst[1] - 1):
            worst = (z.name, cov)
    assert tested >= 5, f"검증할 구역이 {tested}개뿐"
    assert ok / tested >= 0.8, f"{ok}/{tested} · 최악 {worst}"
    return f"{ok}/{tested} 구역이 포착률 90~110%"


@case("⑤구역 집계는 대리지표가 아니다")
def c5():
    import aging
    bl = aging.load()
    z = geo.search("신림7")[0]
    ag = aging.aggregate_zone(bl, z)
    assert ag.unit == "정비구역"
    a = aging.to_area(ag)
    assert a.노후도_대리지표 is False, "구역 경계로 쟀는데 대리지표로 표시됨"
    assert a.면적 is not None and abs(a.면적.value - z.area) < 1
    rep = evaluate(Building(), a)
    r = next(x for x in rep.reqs if x.name == "지역 노후도 비율")
    assert not r.provisional, "구역 기준인데 잠정 표시"
    assert r.verdict is V.MET, r.verdict
    return f"{ag.total:,}동 · 노후 {ag.lo:.1%} · 확정 판정"


@case("⑥구역 내 노후도가 사업 단계를 가른다 (지정=지금도 노후, 는 틀린 전제)")
def c6():
    import aging
    bl = aging.load()
    ps = parcel.load("11620")
    buckets = {"미착공": [], "준공": [], "철거": [], "혼재": []}
    for z in geo.load():
        if z.family != "재개발" or z.sigungu not in ("11620", "11000"):
            continue
        ag = aging.aggregate_zone(bl, z, ps)
        ph = aging.phase_signal(ag)
        if not ph:
            continue
        k = ("철거" if ag.total == 0 else "준공" if ag.hi <= 0.20
             else "미착공" if ag.lo >= Cfg.REDEV_RATIO else "혼재")
        buckets[k].append((z.name, ag.lo, ag.total))
    # 지정요건은 '지정 시점' 기준이라, 준공된 구역이 지금 노후 0% 인 건 정상이다.
    assert buckets["미착공"], "미착공 구역이 하나도 없음"
    assert buckets["준공"] or buckets["철거"], "진행/완료 구역이 하나도 없음"
    for nm, lo, n in buckets["미착공"]:
        assert lo >= Cfg.REDEV_RATIO
    return " · ".join(f"{k} {len(v)}" for k, v in buckets.items() if v)


@case("⑦지번주소와 도로명주소가 같은 곳을 가리킨다")
def c7():
    import addrdb
    a = addrdb.search("관악구 신림동 10-10")
    b = addrdb.search("신림로58길 62-5")
    assert a and b, (len(a), len(b))
    assert a[0].pnu == b[0].pnu, (a[0].pnu, b[0].pnu)
    assert a[0].bjd == "1162010200"
    return f"둘 다 PNU {a[0].pnu}"


@case("⑧주소 → 좌표 → 구역 (키 없이 전 경로)")
def c8():
    import addrdb
    h = addrdb.search("관악구 신림동 675-159")[0]
    la, lo, how = addrdb.coord(h)
    assert la is not None, how
    assert 37.40 < la < 37.55 and 126.85 < lo < 127.00, (la, lo)
    hits = geo.at(la, lo)
    assert hits, "구역이 안 잡힘"
    body, _ = geo.pick(hits)
    assert body and "신림7" in body.name, body.name if body else None
    return f"{la:.5f},{lo:.5f} → {body.name[:20]}"


@case("⑨밴드 근거 재현 — 외필지를 안 가르면 밴드가 부풀려진다")
def c9():
    import csv
    ps = parcel.load("11620")
    rows = list(csv.DictReader(open("03. 표제부_20260828103017.csv", encoding="utf-8-sig")))
    def f(s):
        try:
            return float((s or "").strip())
        except ValueError:
            return 0.0
    solo, multi = [], []
    for r in rows:
        a = f(r["대지면적(㎡)"])
        if a <= 0:
            continue
        k = parcel.pnu_of(r["시군구코드"], r["법정동코드"], r["번"], r["지"])
        if k not in ps:
            continue
        (solo if f(r["외필지수"]) == 0 else multi).append(ps[k].area / a)
    solo.sort(); multi.sort()
    q = lambda v, p: v[int(p * (len(v) - 1))]
    assert 0.93 < q(solo, 0.05) and q(solo, 0.95) < 1.15, (q(solo, .05), q(solo, .95))
    assert q(multi, 0.5) < 0.7, q(multi, 0.5)      # 여러 필지 합이라 절반 수준
    return (f"단일 {q(solo,.05):.3f}~{q(solo,.95):.3f} (n={len(solo):,}) / "
            f"외필지 중앙 {q(multi,.5):.3f} (n={len(multi):,})")


@case("⑩철거된 구역은 '건물 0동'으로 드러난다")
def c10():
    import aging
    bl = aging.load()
    ps = parcel.load("11620")
    empty = []
    for z in geo.load():
        if z.sigungu not in ("11620", "11000") or z.family not in ("재개발", "재건축"):
            continue
        hits = parcel.in_zone(z, ps)
        if len(hits) < 50:
            continue
        ag = aging.aggregate_zone(bl, z, ps)
        if ag.total == 0:
            empty.append((z.name, len(hits)))
    assert empty, "필지는 많은데 건물 0인 구역이 하나도 없음(데이터 확인)"
    return f"{len(empty)}건 — 예: {empty[0][0][:22]} (필지 {empty[0][1]})"


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
