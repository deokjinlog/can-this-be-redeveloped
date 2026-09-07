"""서류 파서 케이스 — 값보다 '못 읽으면 못 읽었다고 하는지' 와 개인정보 처리를 본다."""
from datetime import date

import docparse as DP
from engine import Grade

cases = []


def case(name):
    def deco(fn):
        cases.append((name, fn))
        return fn
    return deco


표준등기 = """
【 갑    구 】        ( 소유권에 관한 사항 )
순위번호   등 기 목 적       접        수            등 기 원 인        권리자 및 기타사항
1         소유권보존        1997년8월21일
                          제45678호                                    소유자 김철수 700101-1234567
2         소유권이전        2011년6월14일          2011년5월20일       소유자 홍길동 800202-2345678
                          제12345호               매매
【 을    구 】        ( 소유권 이외의 권리에 관한 사항 )
1         근저당권설정      2015년1월5일           2015년1월5일        채무자 홍길동
"""

표준초본 = """
주민등록표 (초본)
성명 : 홍길동            주민등록번호 : 800202-2345678
1     서울특별시 강남구 대치동 316 은마아파트 12동 305호        2015년 3월 2일   전입
2     서울특별시 관악구 신림동 675-159 덕원빌라 101호           2021년 6월 1일   전입
"""


@case("①표준 등기부 — 소유권 이력과 최근 취득")
def c1():
    d = DP.parse_deungi(표준등기)
    assert len(d.rows) == 2, [r.kind for r in d.rows]      # 을구 근저당은 안 들어와야
    r = d.최근취득
    assert r.date == date(2011, 6, 14), r.date
    assert r.reason == "매매", r.reason
    assert not d.warn, d.warn
    return f"{len(d.rows)}건 · 최근 {r.date} {r.reason}"


@case("②을구를 소유권으로 읽지 않는다")
def c2():
    d = DP.parse_deungi(표준등기)
    assert not any("근저당" in r.kind for r in d.rows), [r.kind for r in d.rows]
    return "갑구만 " + " / ".join(r.kind for r in d.rows)


@case("③'협의분할에 의한 상속' 을 상속으로 읽는다 (§39② 게이트)")
def c3():
    t = """【 갑 구 】 (소유권에 관한 사항)
1 소유권보존 1985.03.02 제1111호 소유자 박영수 400101-1234567
2 소유권이전 2018-04-10 제7777호 2018-03-15 협의분할에의한상속 소유자 박민준 750505-1234567"""
    d = DP.parse_deungi(t)
    assert d.상속이혼, d.최근취득.reason
    f = DP.to_case_facts(d)
    assert f["취득사유_상속이혼"].value is True
    assert f["취득일"].value == date(2018, 4, 10)
    # '협의분할' 로 끊겨 상속을 놓치면 이 게이트가 안 열린다
    assert "상속" in f["취득사유_상속이혼"].source_span
    return f"{d.최근취득.reason} → 상속 인식"


@case("④매매면 상속 게이트가 열리지 않는다")
def c4():
    d = DP.parse_deungi(표준등기)
    assert not d.상속이혼
    assert "취득사유_상속이혼" not in DP.to_case_facts(d)
    return "매매 → 게이트 닫힘"


@case("⑤날짜 양식 세 가지를 모두 읽는다")
def c5():
    got = []
    for s in ("2011년6월14일", "2011.06.14", "2011-6-14"):
        t = f"【 갑 구 】 (소유권에 관한 사항)\n1 소유권이전 {s} 제1호 매매 소유자 홍길동"
        d = DP.parse_deungi(t)
        assert d.최근취득 and d.최근취득.date == date(2011, 6, 14), (s, d.최근취득)
        got.append(s)
    return " / ".join(got)


@case("⑥못 읽으면 못 읽었다고 한다 (추측 금지)")
def c6():
    d = DP.parse_deungi("등기사항전부증명서\n표제부만 있는 문서")
    assert d.최근취득 is None
    assert d.warn, "경고 없이 조용히 빈 결과"
    assert DP.to_case_facts(d) == {}, "못 읽었는데 Fact 를 발급"
    c = DP.parse_chobon("주민등록표 초본\n(주소 변동 사항 없음)")
    assert c.거주개시() is None and c.warn
    return "등기부·초본 둘 다 경고 + Fact 미발급"


@case("⑦개인정보 마스킹 — 주민번호 뒷자리와 이름")
def c7():
    d = DP.parse_deungi(표준등기)
    joined = " ".join(r.line for r in d.rows)
    assert "1234567" not in joined and "2345678" not in joined, "주민번호 노출"
    assert "-*******" in joined, joined[:80]
    assert "홍길동" not in joined and "김철수" not in joined, "이름 노출"
    assert "홍○○" in joined or "김○○" in joined
    out = DP.render(d)
    assert "1234567" not in out
    return "주민번호 마스킹 · 이름은 성만"


@case("⑧소유권이전이 여러 번이면 최근 것")
def c8():
    t = """【 갑 구 】 (소유권에 관한 사항)
1 소유권보존 1990.01.01 제1호 소유자 갑
2 소유권이전 2005.05.05 제2호 매매 소유자 을
3 소유권이전 2019.09.09 제3호 매매 소유자 병"""
    d = DP.parse_deungi(t)
    assert d.최근취득.date == date(2019, 9, 9), d.최근취득.date
    assert len(d.rows) == 3
    return "3건 중 2019-09-09"


@case("⑨초본 — 지번으로 그 집 전입일을 고른다")
def c9():
    c = DP.parse_chobon(표준초본)
    r = c.거주개시("신림동 675-159")
    assert r and r.date == date(2021, 6, 1), r
    첫 = c.거주개시()          # 지번 없으면 가장 이른 전입
    assert 첫.date == date(2015, 3, 2), 첫
    return f"지번 지정 {r.date} / 미지정 {첫.date}"


@case("⑩engine 연결 — P1 로 들어가고 T 가 아니다")
def c10():
    d = DP.parse_deungi(표준등기)
    c = DP.parse_chobon(표준초본)
    f = DP.to_case_facts(d, c, "신림동 675-159", 발급일=date(2026, 9, 7))
    assert f["취득일"].grade is Grade.P1 and f["거주개시일"].grade is Grade.P1
    assert f["취득일"].grade is not Grade.T
    assert f["취득일"].source_span and f["거주개시일"].source_span, "원문 인용이 비었다"
    assert f["취득일"].doc_asof == date(2026, 9, 7)
    return f"취득 {f['취득일'].value} · 거주 {f['거주개시일'].value} · 둘 다 P1"


@case("⑪서류로 채우면 장기보유 예외가 실제로 판정된다")
def c11():
    from engine import Case, Fact, evaluate
    d = DP.parse_deungi(표준등기)
    c = DP.parse_chobon(표준초본)
    kw = dict(사업유형="재개발", 기준일=date(2026, 9, 7), 기준일_기준="등기일",
              투기과열지구=True, 일세대일주택=Fact(True, Grade.P1, "등기부·주민등록표", "1주택"))
    kw.update(DP.to_case_facts(d, c, "신림동 675-159"))
    rep = evaluate(Case(**kw))
    ex1 = next(e for e in rep.exceptions if e.id == "ex1_장기보유")
    소유 = next(r for r in ex1.reqs if "소유" in r.name)
    assert 소유.verdict.name == "MET", (소유.verdict, 소유.value)   # 2011 → 15.2년
    assert 소유.grade is Grade.P1
    return f"소유 {소유.value} · 등급 {소유.grade.name} · 전체 {rep.overall}"


passed = 0
for name, fn in cases:
    try:
        print(f"  ✅ {name}: {fn()}")
        passed += 1
    except AssertionError as e:
        print(f"  ❌ {name}: {e}")

print(f"\n{passed}/{len(cases)} 통과")
