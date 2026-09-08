"""관악구 — 자체 CMS(bbsNew) + eminwon 연동.

목록·검색 파라미터는 페이지의 게시판 폼(BbsNewFVo)에서 읽어 확인했다:
    tgtTypeCd = NOT_ANCMT_SJ(제목) | NOT_ANCMT_CN(내용)
    searchKey = 검색어 · pageUnit = 10|20|30 · pageIndex = N
목록 행의 상세 키는 링크가 아니라 onclick 이다 — doBbsFView('39963').

첨부는 두 경로로 노출되는데 **synapGosiView.do 는 파일이 아니라 미리보기 뷰어다**
(어떤 확장자로 요청하든 28KB 짜리 "Synap Document Viewer" HTML 이 온다). 실제 파일은
eminwon 직링크(FileDown.jsp) 쪽이므로 그걸 쓰고, 뷰어 링크는 참고용으로만 남긴다.
"""
import re
import urllib.parse

from ..base import Attachment, BaseNoticeAdapter, Notice, hit

BASE = "https://www.gwanak.go.kr"
LIST = BASE + "/site/gwanak/ex/bbsNew/List.do"
VIEW = BASE + "/site/gwanak/ex/bbsNew/View.do"

_ROW = re.compile(
    r'<tr>(.*?)</tr>', re.S)
_ID = re.compile(r"doBbsFView\('(\d+)'\)")
_TITLE = re.compile(r'doBbsFView\(\'\d+\'\);return false;"\s*title="[^"]*">([^<]+)</a>')
_TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


class Gwanak(BaseNoticeAdapter):
    gu = "관악구"
    base = BASE

    def list_page(self, page: int, query: str = "") -> str:
        q = {"typeCode": 1, "pageIndex": page, "pageUnit": 30}
        if query:
            q["tgtTypeCd"] = "NOT_ANCMT_SJ"
            q["searchKey"] = query
        return LIST + "?" + urllib.parse.urlencode(q, encoding="utf-8")

    def parse_list(self, html: str):
        for block in _ROW.findall(html):
            m = _ID.search(block)
            if not m:
                continue
            no = m.group(1)
            t = _TITLE.search(block)
            tds = [self.text(x) for x in _TD.findall(block)]
            title = self.text(t.group(1)) if t else (tds[2] if len(tds) > 2 else "")
            dates = _DATE.findall(" ".join(tds))
            yield Notice(gu=self.gu, no=no, title=title,
                         posted=dates[0] if dates else "",
                         url=self.detail_url(no),
                         doc_no=tds[1] if len(tds) > 1 else "",
                         dept=next((x for x in tds if x.endswith("과")), ""),
                         keywords=hit(title))

    def detail_url(self, no: str) -> str:
        return f"{VIEW}?not_ancmt_mgt_no={no}&typeCode=1"

    @staticmethod
    def _encode(url: str) -> str:
        """쿼리에 날것의 공백·한글이 들어 있어 그대로는 요청이 안 된다."""
        sp = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
        return urllib.parse.urlunsplit(
            (sp.scheme, sp.netloc, urllib.parse.quote(sp.path),
             urllib.parse.urlencode(q, encoding="utf-8"), sp.fragment))

    def parse_detail(self, html: str, n: Notice) -> Notice:
        seen = set()
        # 실제 파일 — eminwon 직링크. 파일명·경로에 공백과 한글이 그대로 들어 있어
        # 인코딩하지 않으면 요청이 깨진다.
        for raw in re.findall(r'href="(https?://eminwon[^"]+FileDown\.jsp[^"]*)"', html):
            url = self._encode(raw.replace("&amp;", "&"))
            if url in seen:
                continue
            seen.add(url)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            name = urllib.parse.unquote((q.get("user_file_nm") or [""])[0]).strip()
            n.attachments.append(Attachment(name=name or f"{n.no}", url=url,
                                            ext=self.ext_of(name)))
        # 뷰어 링크는 파일이 아니다 — 첨부가 하나도 안 잡혔을 때 흔적만 남긴다
        if not n.attachments:
            for path, name in re.findall(
                    r'href="(/synapGosiView\.do\?[^"]*streFileNm=([^"&]+))"', html):
                n.attachments.append(Attachment(
                    name=urllib.parse.unquote(name), url=BASE + path.replace("&amp;", "&"),
                    ext=self.ext_of(name), note="뷰어 링크만 있음(파일 URL 없음)"))
        # 부서·공고일 보강
        t = self.text(html)
        if not n.dept:
            m = re.search(r"담당부서\s*([가-힣]{2,10}과)", t)
            if m:
                n.dept = m.group(1)
        m = re.search(r"공고일자\s*(20\d{2}-\d{2}-\d{2})", t)
        if m:
            n.posted = m.group(1)
        return n
