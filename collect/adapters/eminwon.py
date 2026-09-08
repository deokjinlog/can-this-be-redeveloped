"""eminwon(새올) 직접 조회 — 구청 CMS 를 거치지 않고 원천에서 읽는다.

구청 홈페이지는 CMS 가 제각각이지만 고시공고 데이터는 행안부 새올이 원천이고,
같은 엔드포인트가 호스트만 바꾸면 그대로 동작한다(관악·동작·영등포 실측).

    /emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do
      ?jndinm=OfrNotAncmtEJB&context=NTIS
      &method=selectListOfrNotAncmt&methodnm=selectListOfrNotAncmtHomepage
      &homepage_pbs_yn=Y&subCheck=Y&ofr_pageSize=50
      &not_ancmt_se_code=01,04&not_ancmt_sj={검색어}&pageIndex={N}

⚠ **첨부는 이 경로로 못 받는다.** 상세의 goDownLoad() 인자가 Base64 로 암호화돼 있고,
   세션 쿠키를 유지해도 FileDown.jsp 가 133바이트 오류 HTML 을 돌려준다(실측).
   평문 인자는 구청 CMS 상세에만 노출되므로, 첨부가 필요하면 CMS 어댑터를 써야 한다.
   그래서 이 어댑터의 쓸모는 **메타데이터** 다 — 어느 구역에 언제 어떤 고시가 났는지.
   특히 '해제' 고시는 제목만으로도 값지다(현행 고시도형에는 없는 이력).
"""
import re
import urllib.parse

from ..base import BaseNoticeAdapter, Notice, hit

PATH = "/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do"
COMMON = {"jndinm": "OfrNotAncmtEJB", "context": "NTIS",
          "homepage_pbs_yn": "Y", "subCheck": "Y"}

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_ID = re.compile(r"searchDetail\('(\d+)'\)")
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


class EminwonAdapter(BaseNoticeAdapter):
    """호스트만 바꿔 25개 구에 재사용한다."""

    def __init__(self, gu: str, host: str):
        self.gu = gu
        self.host = host
        self.base = f"https://{host}"

    def _url(self, **kw) -> str:
        q = dict(COMMON)
        q.update(kw)
        return self.base + PATH + "?" + urllib.parse.urlencode(q, encoding="utf-8")

    def list_page(self, page: int, query: str = "") -> str:
        return self._url(method="selectListOfrNotAncmt",
                         methodnm="selectListOfrNotAncmtHomepage",
                         ofr_pageSize=50, not_ancmt_se_code="01,04",
                         not_ancmt_sj=query or "", pageIndex=page)

    def parse_list(self, html: str):
        for block in _ROW.findall(html):
            m = _ID.search(block)
            if not m:
                continue
            tds = [self.text(x) for x in _TD.findall(block)]
            # 순번 / 고시번호 / 제목 / 담당부서 / 등록일 / (빈칸) / 첨부수
            doc_no = tds[1] if len(tds) > 1 else ""
            title = tds[2] if len(tds) > 2 else ""
            dept = tds[3] if len(tds) > 3 else ""
            dates = _DATE.findall(" ".join(tds))
            yield Notice(gu=self.gu, no=m.group(1), title=title,
                         posted=dates[0] if dates else "", url=self.detail_url(m.group(1)),
                         dept=dept, doc_no=doc_no, keywords=hit(title))

    def detail_url(self, no: str) -> str:
        return self._url(method="selectOfrNotAncmt",
                         methodnm="selectOfrNotAncmtRegst", not_ancmt_mgt_no=no)

    def parse_detail(self, html: str, n: Notice) -> Notice:
        """제목은 목록에서 잘려 오므로 상세에서 전체를 다시 읽는다."""
        t = self.text(html)
        m = re.search(r"제목\s+(.+?)(?:\s+내용|\s+파일|\s*$)", t)
        if m and len(m.group(1)) > len(n.title):
            n.title = m.group(1).strip()
            n.keywords = hit(n.title)
        m = re.search(r"담당부서\s+([가-힣]{2,12}과)", t)
        if m:
            n.dept = m.group(1)
        m = re.search(r"등록일\s+(20\d{2}-\d{2}-\d{2})", t)
        if m:
            n.posted = m.group(1)
        # 첨부는 암호화돼 받을 수 없다 — 있다는 사실만 남긴다
        cnt = len(re.findall(r"goDownLoad\('", html))
        if cnt:
            from ..base import Attachment
            n.attachments = [Attachment(
                name=f"(첨부 {cnt}건)", url="", ext="",
                note="eminwon 직접 경로로는 받을 수 없음(인자 암호화) — CMS 필요")]
        return n
