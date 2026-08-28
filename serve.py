"""
serve.py — 로컬 서버: 신호등 HTML 서빙 + 건축물대장 자동수집 프록시

브라우저는 외부 API를 직접 못 부르니(CORS·키노출), 이 로컬 서버가 대신 부른다.
키는 .env 에만 있고 페이지엔 안 나감.

실행:  python serve.py   →  http://localhost:8000  열기
       (자동채움: 시군구/법정동/본번/부번 넣고 "건축물대장 자동채움")
표준 라이브러리만 사용.
"""

import json
import os
import urllib.parse
from http.server import SimpleHTTPRequestHandler
from socketserver import ThreadingTCPServer

import aging as AG
from criteria_engine import Cfg
from gather import fetch_title, _load_env

_load_env()
ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))


# ── 노후도 전수집계 (표제부 CSV) — 최초 요청 때 1회 로딩 후 캐시 ──
_AG_CACHE: dict = {}


def _aging_buckets(by: str):
    if "bldgs" not in _AG_CACHE:
        _AG_CACHE["bldgs"] = AG.load()          # 없으면 SystemExit
        _AG_CACHE["region"] = AG.REGION
    if by not in _AG_CACHE:
        _AG_CACHE[by] = AG.aggregate(_AG_CACHE["bldgs"], by)
    return _AG_CACHE[by]


def _ag_row(a) -> dict:
    return {"key": a.key, "label": a.label, "unit": a.unit, "total": a.total,
            "old": a.old, "unknown": a.unknown,
            "lo": round(a.lo, 4), "hi": round(a.hi, 4),
            "area_lo": round(a.area_lo, 4), "area_hi": round(a.area_hi, 4),
            "연면적합": round(a.연면적합), "필지수": a.필지수,
            "과소필지": a.과소필지, "필지면적미상": a.필지면적미상,
            "verdict": a.verdict(Cfg.REDEV_RATIO),
            "by_decade": a.by_decade, "by_struct": a.by_struct}


def _aging_payload(q: dict) -> dict:
    by = (q.get("by", ["road"])[0] or "road")
    if by not in ("dong", "road", "bun"):
        return {"ok": False, "error": "by 는 dong|road|bun"}
    buckets = _aging_buckets(by)
    key = (q.get("key", [""])[0] or "").strip()
    term = (q.get("q", [""])[0] or "").strip()
    top = int(q.get("top", ["12"])[0] or 12)
    min_total = int(q.get("min", ["10"])[0] or 10)

    base = {"ok": True, "by": by, "region": _AG_CACHE.get("region", ""),
            "need": Cfg.REDEV_RATIO, "need_area": Cfg.NOHU_AREA_RATIO,
            "기준": "표준30", "출처": AG.SRC_DOC, "기준일": AG._BASE.isoformat()}
    if key:
        a = buckets.get(key)
        if a is None:
            return {"ok": False, "error": f"키 {key} 없음"}
        return {**base, "hit": _ag_row(a)}
    items = [a for a in buckets.values() if a.key != "미상"]
    if term:
        items = [a for a in items if term in a.label or term == a.key]
    else:
        items = [a for a in items if a.total >= min_total]
    items.sort(key=lambda a: (-a.lo, -a.total))
    return {**base, "list": [_ag_row(a) for a in items[:top]], "matched": len(items)}


def _building_payload(raw: dict) -> dict:
    y = raw.get("useAprDay")
    year = int(y[:4]) if y and len(str(y)) >= 4 else None
    st, pu = raw.get("struct") or "", raw.get("purpose") or ""
    rc = any(k in st for k in ("철근콘크리트", "철골", "강구조")) and \
        any(k in pu for k in ("공동주택", "아파트", "주택"))
    return {"ok": True, "built": year, "struct": "RC공동주택" if rc else "기타",
            "households": raw.get("households"), "purpose": pu,
            "bldNm": raw.get("bldNm"), "dong": raw.get("_동수")}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = urllib.parse.urlparse(self.path)
        if parts.path == "/api/gather":
            q = urllib.parse.parse_qs(parts.query)
            try:
                raw = fetch_title(q["sigungu"][0], q["bjdong"][0], q["bun"][0], q["ji"][0])
                self._json(_building_payload(raw))
            except SystemExit as e:
                self._json({"ok": False, "error": str(e)}, 200)
            except Exception as e:
                self._json({"ok": False, "error": type(e).__name__ + ": " + str(e)}, 200)
            return
        if parts.path == "/api/aging":
            try:
                self._json(_aging_payload(urllib.parse.parse_qs(parts.query)))
            except SystemExit as e:
                self._json({"ok": False, "error": str(e)}, 200)
            except Exception as e:
                self._json({"ok": False, "error": type(e).__name__ + ": " + str(e)}, 200)
            return
        if parts.path in ("/", ""):
            self.path = "/web/index.html"
        return super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"▶ 재개발 신호등 로컬 서버: http://localhost:{PORT}")
    print("  (건축물대장 자동채움 + 표제부 전수 노후도 활성 · Ctrl+C 종료)")
    with ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료.")
