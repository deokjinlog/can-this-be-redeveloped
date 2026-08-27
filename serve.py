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

from gather import fetch_title, _load_env

_load_env()
ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))


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
        if parts.path in ("/", ""):
            self.path = "/web/index.html"
        return super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"▶ 재개발 신호등 로컬 서버: http://localhost:{PORT}")
    print("  (건축물대장 자동채움 활성 · Ctrl+C 종료)")
    with ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료.")
