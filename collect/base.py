"""구청 고시공고 어댑터의 공통 뼈대.

구청마다 게시판 솔루션이 다르다(자체 CMS / 표준프레임워크 / eminwon 연동…).
공통은 셋뿐이다 — 목록을 넘기고, 상세를 열고, 첨부를 받는다.
"""
import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

# 제목에 이 중 하나라도 있어야 대상. 고시공고 게시판은 잡다한 공고가 대부분이다.
KEYWORDS = ("정비구역", "정비계획", "재개발", "재건축")


def hit(title: str) -> list:
    return [k for k in KEYWORDS if k in (title or "")]


@dataclass
class Notice:
    gu: str
    no: str                  # 게시번호 (상세 조회 키)
    title: str
    posted: str = ""         # 게시일 YYYY-MM-DD
    url: str = ""
    dept: str = ""
    doc_no: str = ""         # 고시·공고 번호
    keywords: list = field(default_factory=list)
    attachments: list = field(default_factory=list)   # [Attachment]


@dataclass
class Attachment:
    name: str
    url: str
    ext: str = ""            # 파일명이 주장하는 확장자
    real: str = ""           # 매직바이트로 확인한 실제 포맷 (이쪽을 믿는다)
    saved: str = ""          # 내려받은 경로
    bytes: int = 0
    note: str = ""


class BaseNoticeAdapter:
    gu = ""                  # 자치구 이름
    base = ""                # 사이트 origin

    def list_page(self, page: int, query: str = "") -> str:
        """목록 페이지 URL."""
        raise NotImplementedError

    def parse_list(self, html: str) -> Iterator[Notice]:
        """목록 HTML → Notice (첨부 없음)."""
        raise NotImplementedError

    def detail_url(self, no: str) -> str:
        raise NotImplementedError

    def parse_detail(self, html: str, n: Notice) -> Notice:
        """상세 HTML → 첨부·부서 등을 채운 Notice."""
        raise NotImplementedError

    # ── 공통 유틸 ──
    @staticmethod
    def text(h: str) -> str:
        import html as H
        return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h))).strip()

    @staticmethod
    def ext_of(name: str) -> str:
        m = re.search(r"\.([A-Za-z0-9]{1,5})\s*$", (name or "").strip())
        return m.group(1).lower() if m else ""
