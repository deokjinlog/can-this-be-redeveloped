"""collect 어댑터·규율 케이스 — 사이트를 치지 않고 저장된 샘플로만 검증한다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collect import http
from collect.adapters import get as get_adapter
from collect.base import hit

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures")


def _fx(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


cases = []


def case(name):
    def deco(fn):
        cases.append((name, fn))
        return fn
    return deco


@case("①robots: 'User-agent' 없는 'Disallow: /' 도 전면 차단으로 읽는다")
def c1():
    # 표준 파서는 어느 UA 에도 귀속되지 않은 규칙을 무시한다 → 영등포가 '허용' 으로 새어나갔다
    assert http._deny_all("Disallow: /") is True
    assert http._deny_all("User-agent: *\nDisallow: /") is False   # 이건 파서가 잡는다
    assert http._deny_all("User-agent: Googlebot\nDisallow: /x/") is False
    assert http._deny_all("# 주석\n\nDisallow: /") is True
    return "고아 Disallow:/ 를 차단으로"


@case("②키워드 필터 — 제목에 하나도 없으면 대상이 아니다")
def c2():
    assert set(hit("신림5구역 재개발사업 정비구역 지정")) == {"정비구역", "재개발"}
    assert set(hit("정비계획 결정 및 정비구역 지정")) == {"정비구역", "정비계획"}
    assert hit("멸실인정 자동차 말소등록 예고") == []
    assert hit("") == []
    return "정비구역·정비계획·재개발·재건축"


@case("③관악 목록 파싱 — onclick 에서 게시번호를 뽑는다")
def c3():
    ad = get_adapter("관악구")
    rows = list(ad.parse_list(_fx("gwanak_list.html")))
    assert len(rows) >= 10, len(rows)
    assert all(r.no.isdigit() for r in rows), [r.no for r in rows[:3]]
    n = next(r for r in rows if r.no == "39963")
    assert "신림5구역" in n.title, n.title
    assert n.posted == "2026-01-15", n.posted
    assert n.dept == "주택과", n.dept
    assert set(n.keywords) >= {"정비구역", "재개발"}, n.keywords
    return f"{len(rows)}건 · [39963] {n.title[:26]}"


@case("④관악 목록 — 링크가 아니라 onclick 이라 href 만 보면 0건이 된다")
def c4():
    html = _fx("gwanak_list.html")
    assert "doBbsFView" in html
    assert 'href="/site/gwanak/ex/bbsNew/View.do' not in html, "href 로 바뀌었으면 파서 수정 필요"
    return "onclick 기반 확인"


@case("⑤관악 상세 — 뷰어가 아니라 실제 파일 URL 을 집는다")
def c5():
    ad = get_adapter("관악구")
    rows = list(ad.parse_list(_fx("gwanak_list.html")))
    n = next(r for r in rows if r.no == "39963")
    ad.parse_detail(_fx("gwanak_view.html"), n)
    assert n.attachments, "첨부를 못 읽음"
    a = n.attachments[0]
    # synapGosiView.do 는 어떤 확장자로 요청해도 28KB 짜리 뷰어 HTML 을 준다 → 쓰면 안 된다
    assert "synapGosiView" not in a.url, f"뷰어 URL 을 첨부로 잡았다: {a.url}"
    assert "FileDown.jsp" in a.url, a.url
    assert a.ext == "pdf" and "공고문" in a.name, (a.ext, a.name)
    # 쿼리에 공백·한글이 그대로 남으면 요청이 깨진다
    assert " " not in a.url and "공고문" not in a.url, "URL 인코딩 안 됨"
    return f"{len(n.attachments)}건 · {a.name} ({a.ext})"


@case("⑥URL 조립 — 검색 파라미터가 폼과 일치한다")
def c6():
    ad = get_adapter("관악구")
    u = ad.list_page(2, "정비구역")
    assert "pageIndex=2" in u and "tgtTypeCd=NOT_ANCMT_SJ" in u, u
    assert "typeCode=1" in u and "pageUnit=30" in u
    assert ad.list_page(1) .find("searchKey") < 0, "검색어 없으면 searchKey 를 붙이지 않는다"
    assert ad.detail_url("39963").endswith("not_ancmt_mgt_no=39963&typeCode=1")
    return "목록·상세 URL"


@case("⑦차단된 구청은 어댑터가 없다 (실수로 긁지 않도록)")
def c7():
    from collect.adapters import ADAPTERS
    assert "동작구" not in ADAPTERS and "영등포구" not in ADAPTERS, list(ADAPTERS)
    return f"등록된 어댑터: {', '.join(ADAPTERS)}"


@case("⑧매칭 규율 — 루트 stage 의 토큰 규칙을 그대로 쓴다")
def c8():
    import stage
    assert stage._tokens("신림5구역 주택정비형 재개발사업 정비구역 지정") == {"신림5"}
    assert stage._tokens("봉천 제14구역 …") == {"봉천14"}
    # 번호가 다르면 서로 안 맞아야 한다(오매칭 방지의 근거)
    assert not (stage._tokens("신림5구역") & stage._tokens("신림7구역"))
    return "'신림5' · '봉천14' 추출"


@case("⑨eminwon 목록 파싱 — 호스트만 바꾸면 어느 구든 같다")
def c9():
    from collect.adapters import eminwon
    ad = eminwon("관악구", "eminwon.gwanak.go.kr")
    rows = list(ad.parse_list(_fx("eminwon_list.html")))
    assert len(rows) >= 4, len(rows)
    n = next(r for r in rows if r.no == "39313")
    assert "봉천제13구역" in n.title and n.dept == "주택과", (n.title, n.dept)
    assert n.posted == "2025-10-24" and "고시 제2025-130호" in n.doc_no, (n.posted, n.doc_no)
    u = ad.list_page(2, "정비구역")
    assert "OfrAction.do" in u and "pageIndex=2" in u and "not_ancmt_sj=" in u
    return f"{len(rows)}건 · [39313] {n.title[:22]}"


@case("⑩eminwon 첨부는 받을 수 없다는 걸 기록한다 (조용히 비우지 않는다)")
def c10():
    from collect.adapters import eminwon
    from collect.base import Notice
    ad = eminwon("관악구", "eminwon.gwanak.go.kr")
    n = Notice(gu="관악구", no="39313", title="봉천제13구역 …")
    ad.parse_detail(_fx("eminwon_view.html"), n)
    assert n.attachments, "첨부 흔적이 없다"
    a = n.attachments[0]
    assert a.url == "" and "암호화" in a.note, (a.url, a.note)
    return a.note[:40]


@case("⑪매직바이트 — 이름이 .hwpx 라도 내용이 HWP 면 hwp 로 다룬다")
def c11():
    import tempfile
    from collect.notices import sniff, verify
    with tempfile.TemporaryDirectory() as d:
        cases = {"a.hwpx": b"\xd0\xcf\x11\xe0" + b"0" * 8,
                 "b.hwpx": b"PK\x03\x04" + b"0" * 8,
                 "c.pdf": b"%PDF-1.4" + b"0" * 8,
                 "d.pdf": b"<!DOCTYPE html><html>"}
        got = {}
        for nm, raw in cases.items():
            p = os.path.join(d, nm)
            open(p, "wb").write(raw)
            got[nm] = sniff(p)
        assert got["a.hwpx"] == "hwp", got          # 확장자에 속지 않는다
        assert got["b.hwpx"] == "hwpx"
        assert got["c.pdf"] == "pdf"
        assert got["d.pdf"] == "html"
        real, why = verify(os.path.join(d, "d.pdf"), "pdf")
        assert real == "" and "HTML" in why, (real, why)
        real, why = verify(os.path.join(d, "a.hwpx"), "hwpx")
        assert real == "hwp" and "내용은 hwp" in why, (real, why)
    return "확장자≠내용을 잡아낸다"


passed = 0
for name, fn in cases:
    try:
        print(f"  ✅ {name}: {fn()}")
        passed += 1
    except AssertionError as e:
        print(f"  ❌ {name}: {e}")
    except Exception as e:
        print(f"  ❌ {name}: {type(e).__name__}: {e}")

print(f"\n{passed}/{len(cases)} 통과")
