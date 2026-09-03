"""진행단계 케이스 — §39② 시점 게이트와 매칭 정직성."""
from datetime import date

import geo
import stage
from engine import Case, Fact, Grade, evaluate

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
            wrong += 1
    assert matched >= 10, matched
    assert wrong == 0, f"토큰 충돌 오매칭 {wrong}건"
    return f"{matched}건 매칭 · 오매칭 0"


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
    unknown = [s.stage for s in ss if s.rank == -1 and s.stage]
    assert not unknown, f"순서 미매핑 단계: {set(unknown)}"
    return f"{len(ss):,}건 · PNU {withpnu/len(ss):.1%} · 미매핑 단계 0"


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
    assert ok / (ok + bad) >= 0.6, f"일치 {ok} / 불일치 {bad}"
    return f"일치 {ok} · 어긋남 {bad} (어긋남은 게시 시차로 정상 발생)"


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
