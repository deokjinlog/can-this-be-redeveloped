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


# ── 주소 한 줄 → 전체 (juso → geo 구역 → 대장 → 노후도) ──

def _zone_row(z) -> dict:
    return {"name": z.name, "kind": z.kind, "family": z.family, "area": z.area,
            "notice": z.notice, "notice_date": z.notice_date, "created": z.created,
            "parts": z.parts, "gu": z.gu,
            "current": z.현행, "superseded_by": z.superseded_by}


def _address_payload(q: dict) -> dict:
    kw = (q.get("q", [""])[0] or "").strip()
    if not kw:
        return {"ok": False, "error": "주소를 입력하세요"}
    mock = q.get("mock", ["0"])[0] in ("1", "true")
    import juso
    warn = []

    try:
        hits = juso.search(kw, mock)
    except SystemExit as e:
        return {"ok": False, "error": str(e)}
    if not hits:
        return {"ok": False, "error": "주소 검색 결과 없음 — 더 구체적으로 (예: 관악구 신림동 10-10)"}
    a = juso.coord(hits[0], mock)

    out = {"ok": True, "addr": {
        "road": a.roadAddr, "jibun": a.jibunAddr, "admCd": a.admCd, "gu": a.자치구,
        "sigungu": a.sigungu, "bjdong": a.bjdong, "bun": a.bun, "ji": a.ji,
        "lat": a.lat, "lon": a.lon, "crs": a.crs, "bdNm": a.bdNm},
        "alts": [h.roadAddr for h in hits[1:5]]}

    # 구역
    zones = []
    if a.lat is not None:
        try:
            import geo
            zones = geo.at(a.lat, a.lon)
        except SystemExit as e:
            warn.append(f"구역 데이터 없음: {e}")
    else:
        warn.append("좌표 미확인 → 구역 판정 건너뜀 (JUSO_COORD_KEY 필요)")
    body = promo = None
    if zones:
        import geo
        body, promo = geo.pick(zones)
    out["zones"] = [_zone_row(z) for z in zones]
    out["designated"] = body is not None
    out["promo"] = promo is not None
    out["type"] = "재건축" if (body and body.family == "재건축") else "재개발"
    out["zoneArea"] = body.area if body else None
    out["zoneName"] = body.name if body else (promo.name if promo else None)
    out["zoneKind"] = body.kind if body else (promo.kind if promo else None)
    out["notice"] = body.notice if body else None
    out["noticeDate"] = body.notice_date if body else None
    out["superseded"] = (body.superseded_by if body and not body.현행 else None)
    out["outOfCsv"] = False

    # 건축물대장
    try:
        out["building"] = _building_payload(fetch_title(a.sigungu, a.bjdong, a.bun, a.ji, mock))
    except SystemExit as e:
        out["building"] = {"ok": False, "error": str(e)}
        warn.append(f"건축물대장: {e}")
    except Exception as e:
        out["building"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        warn.append(f"건축물대장: {type(e).__name__}")

    # 진행단계 (정보몽땅) — C게이트 §39② 시점 요건까지 여기서 판정된다
    out["site"] = None
    if body is not None:
        try:
            import stage
            st = stage.match_zone(body)
            if st:
                gate, why = st.승계제한
                out["site"] = {"name": st.name, "kind": st.kind, "law": st.law,
                               "stage": st.stage, "rank": st.rank,
                               "진행률": st.진행률, "op": st.op,
                               "gate": gate, "why": why,
                               "note": stage.match_note(body, st)}
                if st.cafe:
                    try:
                        import elapse
                        e = elapse.load().get(st.cafe)
                        if e:
                            out["site"]["anchors"] = e.anchors
                    except Exception:
                        pass
        except SystemExit as e:
            warn.append(f"진행단계: {e}")
        except Exception as e:
            warn.append(f"진행단계: {type(e).__name__}: {e}")

    # 노후도 — 구역이 있으면 그 경계 안 실측(대리지표 아님), 없으면 길 단위
    out["aging"] = None
    out["agingZone"] = False
    out["phase"] = None
    try:
        if body is not None:
            if "bldgs" not in _AG_CACHE:
                _AG_CACHE["bldgs"] = AG.load()
            ag = AG.aggregate_zone(_AG_CACHE["bldgs"], body)
            out["aging"] = _ag_row(ag)
            out["agingZone"] = True
            out["outOfCsv"] = ag.범위밖
            j = ag.jijeok
            if j:
                out["aging"]["jijeok"] = {
                    "필지": j.필지, "면적합": round(j.면적합), "포착률": round(j.포착률, 4),
                    "과소_lo": round(j.과소_lo, 4), "과소_hi": round(j.과소_hi, 4),
                    "경계필지": j.경계필지, "도로필지": j.도로필지,
                    "접도분모": j.접도분모, "접도충족": j.접도충족}
            out["aging"]["접도율"] = ag.접도율
            out["aging"]["호수밀도"] = ag.호수밀도
            ph = AG.phase_signal(ag)
            if ph:
                out["phase"] = {"icon": ph[0], "label": ph[1], "why": ph[2]}
                if out["site"]:
                    import stage as _stg
                    st_obj = next((x for x in _stg.load()
                                   if x.name == out["site"]["name"]), None)
                    cc = AG.cross_check(ag, st_obj) if st_obj else None
                    if cc:
                        out["phase"]["cross"] = {"ok": cc[0], "text": cc[1]}
        else:
            buckets = _aging_buckets("road")
            ag = buckets.get(a.rnMgtSn)
            if ag is None:
                buckets = _aging_buckets("bun")
                ag = buckets.get(a.bun.lstrip("0") or "0")
            out["aging"] = _ag_row(ag) if ag else None
            if ag is None:
                warn.append("이 주소의 도로·지번블록은 가진 표제부 CSV 범위 밖")
    except SystemExit as e:
        warn.append(f"노후도: {e}")
    except Exception as e:
        warn.append(f"노후도: {type(e).__name__}: {e}")
    out["warn"] = warn
    return out


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
        if parts.path == "/api/address":
            try:
                self._json(_address_payload(urllib.parse.parse_qs(parts.query)))
            except SystemExit as e:
                self._json({"ok": False, "error": str(e)}, 200)
            except Exception as e:
                self._json({"ok": False, "error": type(e).__name__ + ": " + str(e)}, 200)
            return
        if parts.path == "/api/zonesearch":
            try:
                import geo
                qs = urllib.parse.parse_qs(parts.query)
                term = (qs.get("q", [""])[0] or "").strip()
                zs = geo.search(term) if term else []
                self._json({"ok": True, "list": [_zone_row(z) for z in zs[:20]],
                            "matched": len(zs)})
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
    print("  (주소 한 줄 조회 + 정비구역 판정 + 대장 자동채움 + 전수 노후도 · Ctrl+C 종료)")
    with ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료.")
