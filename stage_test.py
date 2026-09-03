"""진행단계 케이스 — §39② 시점 게이트와 매칭 정직성."""
from datetime import date

import geo
import stage
from engine import Case, Fact, Grade, V, evaluate

cases = []


def case(name):
    def deco(fn):
        cases.append((name, fn))
        return fn
    return deco


def _site(kind, st, name="테스트구역", gu="관악구"):
    return stage.Site(gu, kind, stage.LAW.get(kind, "기타"), name, "신림동 1",
                      st, stage.ORDER.get(st, -1), "운영", "조합")


@case("①단계 순서 — 절차 흐름대로 단조 증가")
def c1():
    seq = ["정비계획 수립", "정비구역지정", "추진위원회승인", "조합설립인가",
           "사업시행인가", "관리처분인가", "착공", "준공인가", "이전고시", "조합청산"]
    r = [stage.ORDER[s] for s in seq]
    assert r == sorted(r), r
    assert stage.GATE_재건축 == stage.ORDER["조합설립인가"]
    assert stage.GATE_재개발 == stage.ORDER["관리처분인가"]
    assert stage.GATE_재건축 < stage.GATE_재개발
    return f"재건축 게이트 {stage.GATE_재건축} < 재개발 게이트 {stage.GATE_재개발}"


@case("②재건축은 조합설립인가부터, 재개발은 관리처분인가부터 제한")
def c2():
    assert _site("재건축", "추진위원회승인").승계제한[0] == "미발동"
    assert _site("재건축", "조합설립인가").승계제한[0] == "발동"
    assert _site("재개발(주택정비형)", "조합설립인가").승계제한[0] == "미발동"
    assert _site("재개발(주택정비형)", "사업시행인가").승계제한[0] == "미발동"
    assert _site("재개발(주택정비형)", "관리처분인가").승계제한[0] == "발동"
    return "같은 '조합설립인가'라도 재건축=발동 / 재개발=미발동"


@case("③소규모정비·지역주택은 도시정비법 §39② 대상이 아니다")
def c3():
    for k in ("가로주택정비", "소규모재건축", "소규모재개발"):
        st, why = _site(k, "관리처분인가").승계제한
        assert st == "해당없음", (k, st)
        assert "소규모" in why
    for k in ("지역주택", "리모델링"):
        assert _site(k, "조합설립인가").승계제한[0] == "해당없음"
    return "소규모 3종 + 지역주택·리모델링 → 스코프 밖으로 분리"


@case("④단계 미상이면 확인필요 — 미발동으로 반올림하지 않는다")
def c4():
    s = _site("재건축", "")
    assert s.rank == -1 and s.승계제한[0] == "확인필요", s.승계제한
    s2 = _site("재개발(주택정비형)", "조합원 모집신고")
    assert s2.승계제한[0] == "확인필요"
    return "빈 단계·지역주택 표기 → 확인필요"


@case("⑤engine: 제한 미발동이면 8예외를 따지지 않는다")
def c5():
    base = dict(사업유형="재개발", 기준일=date(2026, 9, 1), 기준일_기준="등기일",
                투기과열지구=True)
    없음 = evaluate(Case(**base))
    미발동 = evaluate(Case(**base, 제한발동=Fact(
        False, Grade.S1, "정보몽땅", "현재 '정비구역지정'")))
    assert "가능" in 미발동.overall, 미발동.overall
    assert len(미발동.exceptions) == 1 and 미발동.exceptions[0].id == "gate39"
    assert len(없음.exceptions) == 8, len(없음.exceptions)
    return f"게이트 없음 → 8예외 판정 / 미발동 → 즉시 '{미발동.overall}'"


@case("⑥engine: 제한 발동이면 기존 8예외 경로 그대로")
def c6():
    rep = evaluate(Case(사업유형="재건축", 기준일=date(2026, 9, 1), 기준일_기준="등기일",
                        투기과열지구=True,
                        제한발동=Fact(True, Grade.S1, "정보몽땅", "현재 '조합설립인가'")))
    assert len(rep.exceptions) == 8, len(rep.exceptions)
    assert any("제한 발동" in n for n in rep.notes), rep.notes
    return f"8예외 유지 · {rep.overall}"


@case("⑦구역↔사업장 매칭 — 번호가 어긋나면 붙이지 않는다")
def c7():
    ss = stage.load()
    zs = geo.load()
    try:
        import parcel
        ps = parcel.load("11620")
    except SystemExit as e:
        raise SystemExit(f"필지 데이터 필요: {e}")
    matched = wrong = 0
    for z in zs:
        if z.sigungu not in ("11620", "11000") or z.family not in ("재개발", "재건축"):
            continue
        if len(parcel.in_zone(z, ps)) < 3:
            continue
        m = stage.match_zone(z, ss, ps)
        if not m:
            continue
        matched += 1
        zt, mt = stage._tokens(z.name), stage._tokens(m.name)
        if zt and mt and not (zt & mt):
            # 붙이더라도 반드시 경고가 달려야 한다 — 조용한 오매칭이 0이어야 한다
            assert stage.match_note(z, m), f"경고 없이 붙음: {z.name} → {m.name}"
            wrong += 1
    assert matched >= 10, matched
    return f"{matched}건 매칭 · 번호 어긋남 {wrong}건은 전부 경고 표시됨"


@case("⑧토큰 추출 — 구역 번호가 곧 식별자")
def c8():
    assert stage._tokens("신림7구역 재개발정비사업") == {"신림7"}
    assert stage._tokens("봉천제4-1-2구역 주택재개발정비사업 조합") == {"봉천4-1-2"}
    assert stage._tokens("재건축사업구역") == set()
    return "'신림7' · '봉천4-1-2' · 무명은 빈 집합"


@case("⑨전체 적재 + PNU 조립률")
def c9():
    ss = stage.load()
    assert len(ss) > 1000, len(ss)
    withpnu = sum(1 for s in ss if s.pnu)
    assert withpnu / len(ss) > 0.99, withpnu / len(ss)
    laws = {s.law for s in ss}
    assert {"재건축", "재개발", "소규모", "기타"} == laws, laws
    # 순서를 모르는 단계는 억지로 끼워넣지 않는다 — 대신 전부 '확인필요' 로 흘러야 한다
    unknown = {s.stage for s in ss if s.rank == -1 and s.stage}
    for s_ in ss:
        if s_.rank == -1 and s_.law in ("재개발", "재건축"):
            assert s_.승계제한[0] == "확인필요", (s_.stage, s_.승계제한)
    return (f"{len(ss):,}건 · PNU {withpnu/len(ss):.1%} · "
            f"미매핑 {len(unknown)}종은 전부 확인필요로 흐름")


@case("⑩추정 신호 ↔ 게시 단계 교차검증")
def c10():
    import aging
    import parcel
    bl = aging.load()
    ps = parcel.load("11620")
    ss = stage.load()
    ok = bad = 0
    for z in geo.load():
        if z.sigungu not in ("11620", "11000") or z.family not in ("재개발", "재건축"):
            continue
        site = stage.match_zone(z, ss, ps)
        if not site:
            continue
        ag = aging.aggregate_zone(bl, z, ps)
        cc = aging.cross_check(ag, site)
        if cc is None:
            continue
        ok += cc[0]
        bad += (not cc[0])
    assert ok + bad >= 5, f"검증쌍 {ok+bad}개뿐"
    assert ok >= 3, f"일치가 {ok}건뿐 — 신호나 매칭이 깨졌을 수 있음"
    # 어긋남은 정상이다(고시도형이 옛 구역까지 담고 있어서). 숨기지 않고 사유를 다는지 본다.
    if bad:
        z = next(z for z in geo.load()
                 if z.sigungu in ("11620", "11000") and z.family in ("재개발", "재건축")
                 and (lambda s: s and (lambda c: c and not c[0])(
                     aging.cross_check(aging.aggregate_zone(bl, z, ps), s)))(
                     stage.match_zone(z, ss, ps)))
        s2 = stage.match_zone(z, ss, ps)
        cc = aging.cross_check(aging.aggregate_zone(bl, z, ps), s2)
        assert "옛 구역과 현행 구역" in cc[1], cc[1]
    return f"일치 {ok} · 어긋남 {bad} (어긋남에 사유가 붙는지까지 확인)"


@case("⑪추진경과 — 변경 인가가 여러 번이면 최초 인가일")
def c11():
    import elapse
    e = elapse.Elapse("t", "테스트")
    e.events = [["조합설립인가", "2015-03-01", "인가신청", ""],
                ["조합설립인가", "2015-06-10", "인가", ""],
                ["조합설립인가", "2019-08-20", "(변경)인가", ""],
                ["조합설립인가", "2020-01-05", "(변경)인가고시", ""]]
    assert e.first("조합설립인가") == "2015-06-10", e.first("조합설립인가")
    assert e.anchors["조합설립인가일"] == "2015-06-10"
    return "신청·고시 제외하고 최초 인가 2015-06-10"


@case("⑫준공은 날짜가 아니라 bool 로 들어간다(engine 계약)")
def c12():
    import elapse
    e = elapse.Elapse("t", "테스트")
    e.events = [["준공인가", "2019-08-29", "인가", ""],
                ["착공신고", "2016-06-21", "착공신고", ""]]
    f = elapse.to_case_facts(e)
    assert f["준공"].value is True, f["준공"].value
    assert hasattr(f["착공일"].value, "year"), f["착공일"].value
    assert "준공일" not in f
    return "준공=True(bool) · 착공일=date"


@case("⑬3년이 안 지났으면 '확인필요'가 아니라 불성립")
def c13():
    from datetime import date as D
    from engine import Case, Fact, Grade, V, evaluate
    def run(인가일):
        return evaluate(Case(사업유형="재건축", 기준일=D(2026, 9, 3), 기준일_기준="등기일",
                             투기과열지구=True,
                             제한발동=Fact(True, Grade.S1, "정보몽땅", "조합설립인가"),
                             사업시행계획인가일=Fact(인가일, Grade.S1, "추진경과", "")))
    최근 = run(D(2025, 2, 27))       # 1.5년
    오래 = run(D(2020, 1, 1))        # 6.7년
    r1 = next(e for e in 최근.exceptions if e.id.startswith("ex6"))
    r2 = next(e for e in 오래.exceptions if e.id.startswith("ex6"))
    assert r1.reqs[0].verdict is V.NOT_MET, r1.reqs[0].verdict
    assert "3년 미경과" in (r1.reqs[0].value or ""), r1.reqs[0].value
    assert r2.reqs[0].verdict is V.INSUFFICIENT, r2.reqs[0].verdict
    return "1.5년→불성립(자료 요청 안 함) / 6.7년→착공 이력 요청"


@case("⑭구역ID(AGZ)는 최후 수단 — 이름·위치가 먼저")
def c14():
    import geo
    ss = stage.load()
    zs = geo.load()
    agz_z = {z.agz for z in zs if z.agz}
    agz_s = {s.agz for s in ss if s.agz}
    assert len(agz_z & agz_s) > 100, len(agz_z & agz_s)
    # 이름 토큰이 맞는 사업장이 따로 있으면 AGZ 보다 그쪽이 우선이어야 한다
    z = next(x for x in zs if x.name == "봉천6구역주택재개발사업")
    m = stage.match_zone(z, ss)
    assert m is not None
    note = stage.match_note(z, m)
    assert note and "이름 번호가 다름" in note, note
    return f"AGZ 교집합 {len(agz_z & agz_s)}건 · 어긋나면 경고를 단다"


@case("⑮목록 직접 조회 — cafe·구역ID 가 실려 있다")
def c15():
    ss = stage.load()
    cafes = sum(1 for s in ss if s.cafe)
    agz = sum(1 for s in ss if s.agz)
    assert cafes / len(ss) > 0.95, cafes / len(ss)
    assert agz > 300, agz
    assert len({s.cafe for s in ss if s.cafe}) > 1000
    return f"카페 {cafes:,}/{len(ss):,} · 구역ID {agz:,}"


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
