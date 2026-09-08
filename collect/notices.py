"""구청 고시공고 수집 — 목록 → 상세 → 첨부.

검색어 4개(정비구역·정비계획·재개발·재건축)로 각각 훑고 게시번호로 합친다.
검색이 막힌 구청이면 전체 목록을 돌면서 제목으로 거른다(어댑터가 query="" 를 받는 경우).
"""
import csv
import os
import re
import urllib.parse
from datetime import date, timedelta

from . import http, paths
from .adapters import get as get_adapter
from .base import KEYWORDS

COLS = ["구청", "게시번호", "제목", "게시일", "부서", "공고번호", "검색어",
        "상세URL", "첨부파일명", "확장자", "실제포맷", "첨부URL", "저장경로", "바이트", "비고"]

# 받은 게 정말 그 포맷인지 — 확장자만 믿으면 뷰어 HTML 을 hwpx 로 쌓게 된다
MAGIC = {"pdf": b"%PDF", "hwpx": b"PK", "docx": b"PK", "xlsx": b"PK", "zip": b"PK",
         "hwp": b"\xd0\xcf\x11\xe0", "doc": b"\xd0\xcf\x11\xe0",
         "xls": b"\xd0\xcf\x11\xe0"}


def sniff(path: str) -> str:
    """머리 몇 바이트로 실제 포맷을 본다. 확장자는 참고만 한다.

    관악구 첨부에는 이름이 .hwpx 인데 내용은 구형 HWP(OLE) 인 파일이 섞여 있다.
    확장자를 믿고 버리면 멀쩡한 고시문을 잃는다.
    """
    try:
        head = open(path, "rb").read(64)      # '<!doctype' 만 해도 9바이트다
    except OSError:
        return ""
    low = head.lstrip()[:32].lower()
    if low.startswith((b"<!doctype", b"<html", b"<?xml", b"<script", b"\r\r")):
        return "html"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "hwp"
    if head.startswith(b"PK"):
        return "hwpx"
    return ""


def verify(path: str, ext: str) -> tuple[str, str]:
    """(실제 포맷, 문제 사유). HTML·빈 파일만 실패로 본다."""
    real = sniff(path)
    if real == "html":
        return "", "HTML 을 받음(뷰어·오류 페이지)"
    if not real:
        return "", "포맷을 알 수 없음"
    if real != ext:
        return real, f"확장자는 {ext} 인데 내용은 {real}"
    return real, ""


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", (name or "첨부")).strip()[:80]


def collect(gu: str, pages: int = 3, since: str = None,
            queries=KEYWORDS, download: bool = True, limit: int = None) -> dict:
    """한 구청 수집. since='2024-09-08' 이면 그 이후 게시물만."""
    ad = get_adapter(gu)
    found: dict = {}
    per_query: dict = {}

    for q in queries:
        n_before = len(found)
        for p in range(1, pages + 1):
            url = ad.list_page(p, q)
            try:
                html = http.get(url)
            except http.Blocked as e:
                raise SystemExit(str(e))
            rows = list(ad.parse_list(html))
            if not rows:
                break
            for n in rows:
                if not n.keywords:
                    continue
                if since and n.posted and n.posted < since:
                    continue
                cur = found.get(n.no)
                if cur is None:
                    found[n.no] = n
                else:
                    cur.keywords = sorted(set(cur.keywords) | set(n.keywords))
        per_query[q] = len(found) - n_before

    items = sorted(found.values(), key=lambda n: n.posted or "", reverse=True)
    # limit 은 상세·첨부에만 건다. 목록(index.csv)까지 자르면 나중에 이어받을 근거가 사라진다.
    targets = items[:limit] if limit else items
    todo = {n.no for n in targets}

    # 상세 → 첨부
    raw_dir = paths.gu_dir(gu, "raw")
    for n in targets:
        try:
            html = http.get(n.url)
        except Exception as e:
            n.attachments = []
            continue
        ad.parse_detail(html, n)
        if since and n.posted and n.posted < since:
            continue
        for a in n.attachments:
            if not download:
                continue
            dest = os.path.join(raw_dir, f"{n.no}_{_safe(a.name)}")
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                a.saved, a.bytes, a.note = dest, os.path.getsize(dest), "캐시"
                continue
            try:
                size, ctype = http.download(a.url, dest, referer=n.url)
                a.saved, a.bytes = dest, size
                real, bad = verify(dest, a.ext)
                if not real:
                    a.note = bad
                    os.remove(dest)          # 파일이 아닌 건 남기지 않는다
                    a.saved = ""
                else:
                    a.real = real            # 추출은 확장자가 아니라 이걸로 한다
                    if bad:
                        a.note = bad
                    elif size < 1024:
                        a.note = f"의심(작음, {ctype})"
            except http.Blocked as e:
                a.note = "robots 차단"
            except Exception as e:
                a.note = f"실패: {type(e).__name__}"

    out = os.path.join(paths.gu_dir(gu), "index.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        for n in items:
            if not n.attachments:
                why = "첨부없음" if n.no in todo else "상세 미조회(limit)"
                w.writerow([n.gu, n.no, n.title, n.posted, n.dept, n.doc_no,
                            "|".join(n.keywords), n.url, "", "", "", "", "", "", why])
            for a in n.attachments:
                w.writerow([n.gu, n.no, n.title, n.posted, n.dept, n.doc_no,
                            "|".join(n.keywords), n.url, a.name, a.ext, a.real, a.url,
                            a.saved, a.bytes, a.note])
    return {"gu": gu, "notices": len(items), "detailed": len(targets), "index": out,
            "per_query": per_query,
            "attachments": sum(len(n.attachments) for n in items)}
