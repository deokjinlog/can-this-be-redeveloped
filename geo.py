"""
geo.py — 정비구역 경계(SHP) 리더 · 좌표변환 · 점-구역 판정

"이 주소가 이미 정비구역 안인가" 를 답하고, 요건모듈 A 의 [필수] '구역 면적' 을
추정이 아니라 고시도형의 실측치(DGM_AR)로 채운다.

원천: 서울 열린데이터광장 '서울시 의제처리구역 위치정보'(OA-20957, UPIS_C_UQ181).
      정비구역·재정비촉진지구·도시개발구역 등 20종 지정도형 3,209건.
좌표계: EPSG:2097 (Korean 1985 / Modified Korea Central Belt, Bessel 1841).
      → 폴리곤은 TM 그대로 두고, 질의 좌표(WGS84)를 TM 으로 넣어 비교한다(싸고 정확).

의존성 없음 — SHP/DBF 파서, TM 투영, 데이텀 변환 모두 표준 라이브러리로 직접 구현.

사용:
  python geo.py --setup <경로.zip|디렉터리>   # SHP → data/zones-seoul.json 로 1회 변환
  python geo.py --search 신림                # 이름으로 구역 찾기
  python geo.py --at 37.4842 126.9295        # 이 좌표가 어느 구역 안인가
  python geo.py --sigungu 11620              # 자치구 코드로 목록
  python geo.py --geojson out.geojson        # 지도용 WGS84 GeoJSON 내보내기
"""

import json
import math
import os
import re
import struct
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
ZONE_JSON = os.path.join(ROOT, "data", "zones-seoul.json")
SRC_DOC = "서울시 의제처리구역 위치정보(UPIS_C_UQ181, 고시도형)"


# ══════════════════════════════════════════════════════════════════
# 1. 좌표변환 — EPSG:2097 ↔ WGS84
# ══════════════════════════════════════════════════════════════════

class _Ellipsoid:
    def __init__(self, a, invf):
        self.a = a
        self.f = 1.0 / invf
        self.e2 = self.f * (2 - self.f)
        self.ep2 = self.e2 / (1 - self.e2)


BESSEL = _Ellipsoid(6377397.155, 299.1528128)     # Korean Datum 1985
GRS80 = _Ellipsoid(6378137.0, 298.257223563)      # Korea 2000 (≈ WGS84)
WGS84 = _Ellipsoid(6378137.0, 298.257223563)

# Bessel(한국) → WGS84 지심 3-parameter (국토지리정보원 통용값, ±1~2m)
DX, DY, DZ = -146.43, 507.89, 681.46


@dataclass(frozen=True)
class CRS:
    """횡메르카토르 투영 정의 + WGS84 로 가는 데이텀 이동량."""
    name: str
    el: _Ellipsoid
    lat0: float
    lon0: float
    k0: float
    fe: float
    fn: float
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    note: str = ""


# 우리 구역 도형
EPSG_2097 = CRS("EPSG:2097", BESSEL, 38.0, 127.0028902777778, 1.0, 200000, 500000,
                DX, DY, DZ, "Korean 1985 / Modified Central Belt — 의제처리구역 SHP")
# juso 좌표제공 API 가 어느 것을 주는지는 키를 받아 실측으로 확정한다(추측 금지).
EPSG_5174 = CRS("EPSG:5174", BESSEL, 38.0, 127.0028902777778, 1.0, 200000, 500000,
                DX, DY, DZ, "Korean 1985 중부원점 (2097 과 같은 파라미터)")
EPSG_5181 = CRS("EPSG:5181", GRS80, 38.0, 127.0, 1.0, 200000, 500000,
                note="Korea 2000 중부원점")
EPSG_5186 = CRS("EPSG:5186", GRS80, 38.0, 127.0, 1.0, 200000, 600000,
                note="Korea 2000 중부원점 2010 (N 600,000)")
EPSG_5179 = CRS("EPSG:5179", GRS80, 38.0, 127.5, 0.9996, 1000000, 2000000,
                note="UTM-K")

CANDIDATES = [EPSG_5186, EPSG_5181, EPSG_5174, EPSG_5179]
BASE = EPSG_2097          # 구역 폴리곤이 담긴 좌표계


def _arc(el, phi):
    """자오선호 길이."""
    e2 = el.e2
    return el.a * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
                   - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
                   + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
                   - (35 * e2**3 / 3072) * math.sin(6 * phi))


_M0: dict = {}


def _m0(c: CRS) -> float:
    if c.name not in _M0:
        _M0[c.name] = _arc(c.el, math.radians(c.lat0))
    return _M0[c.name]


def tm_to_geodetic(x, y, c: CRS = None):
    """TM → 그 데이텀의 경위도(도)."""
    c = c or BASE
    el = c.el
    e2, ep2, a = el.e2, el.ep2, el.a
    M = _m0(c) + (y - c.fn) / c.k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
          + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
          + (151 * e1**3 / 96) * math.sin(6 * mu)
          + (1097 * e1**4 / 512) * math.sin(8 * mu))
    C1 = ep2 * math.cos(p1) ** 2
    T1 = math.tan(p1) ** 2
    sn = math.sin(p1)
    N1 = a / math.sqrt(1 - e2 * sn * sn)
    R1 = a * (1 - e2) / (1 - e2 * sn * sn) ** 1.5
    D = (x - c.fe) / (N1 * c.k0)
    lat = p1 - (N1 * math.tan(p1) / R1) * (
        D**2 / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1**2 - 9 * ep2) * D**4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1**2 - 252 * ep2 - 3 * C1**2) * D**6 / 720)
    lon = math.radians(c.lon0) + (
        D - (1 + 2 * T1 + C1) * D**3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1**2 + 8 * ep2 + 24 * T1**2) * D**5 / 120) / math.cos(p1)
    return math.degrees(lat), math.degrees(lon)


def geodetic_to_tm(lat, lon, c: CRS = None):
    """그 데이텀의 경위도(도) → TM."""
    c = c or BASE
    el = c.el
    e2, ep2, a = el.e2, el.ep2, el.a
    p, l = math.radians(lat), math.radians(lon)
    sn, cs = math.sin(p), math.cos(p)
    N = a / math.sqrt(1 - e2 * sn * sn)
    T = math.tan(p) ** 2
    C = ep2 * cs * cs
    A = (l - math.radians(c.lon0)) * cs
    M = _arc(el, p)
    x = c.fe + c.k0 * N * (A + (1 - T + C) * A**3 / 6
                           + (5 - 18 * T + T**2 + 72 * C - 58 * ep2) * A**5 / 120)
    y = c.fn + c.k0 * (M - _m0(c) + N * math.tan(p) * (
        A**2 / 2 + (5 - T + 9 * C + 4 * C**2) * A**4 / 24
        + (61 - 58 * T + T**2 + 600 * C - 330 * ep2) * A**6 / 720))
    return x, y


def _to_ecef(lat, lon, el, h=0.0):
    p, l = math.radians(lat), math.radians(lon)
    s = math.sin(p)
    N = el.a / math.sqrt(1 - el.e2 * s * s)
    return ((N + h) * math.cos(p) * math.cos(l),
            (N + h) * math.cos(p) * math.sin(l),
            (N * (1 - el.e2) + h) * s)


def _from_ecef(X, Y, Z, el):
    lon = math.atan2(Y, X)
    p = math.hypot(X, Y)
    lat = math.atan2(Z, p * (1 - el.e2))
    for _ in range(6):        # 수렴 빠름
        s = math.sin(lat)
        N = el.a / math.sqrt(1 - el.e2 * s * s)
        h = p / math.cos(lat) - N
        lat = math.atan2(Z, p * (1 - el.e2 * N / (N + h)))
    s = math.sin(lat)
    N = el.a / math.sqrt(1 - el.e2 * s * s)
    return math.degrees(lat), math.degrees(lon), p / math.cos(lat) - N


def tm_to_wgs84(x, y, c: CRS = None):
    """TM → WGS84 경위도."""
    c = c or BASE
    lat, lon = tm_to_geodetic(x, y, c)
    if not (c.dx or c.dy or c.dz):
        return lat, lon
    X, Y, Z = _to_ecef(lat, lon, c.el)
    la, lo, _ = _from_ecef(X + c.dx, Y + c.dy, Z + c.dz, WGS84)
    return la, lo


def wgs84_to_tm(lat, lon, c: CRS = None):
    """WGS84 경위도 → TM."""
    c = c or BASE
    if not (c.dx or c.dy or c.dz):
        return geodetic_to_tm(lat, lon, c)
    X, Y, Z = _to_ecef(lat, lon, WGS84)
    la, lo, _ = _from_ecef(X - c.dx, Y - c.dy, Z - c.dz, c.el)
    return geodetic_to_tm(la, lo, c)


KOREA_BOX = (33.0, 43.0, 124.0, 132.0)          # lat_min, lat_max, lon_min, lon_max


def sniff_crs(x: float, y: float, near=None, cands=None):
    """정체 모를 TM 좌표 → 어느 좌표계인지 후보 중에서 고른다.

    juso 좌표제공 API 가 어떤 원점을 주는지 문서마다 달라, 추측하지 않고
    '한국 범위 안에 떨어지는가 / (알면) 기대 지점과 얼마나 가까운가' 로 판별한다.
    반환: [(CRS, lat, lon, 기대점과의 거리 m 또는 None)] — 그럴듯한 순.
    """
    out = []
    for c in (cands or CANDIDATES):
        try:
            la, lo = tm_to_wgs84(x, y, c)
        except (ValueError, ZeroDivisionError):
            continue
        if not (KOREA_BOX[0] <= la <= KOREA_BOX[1] and KOREA_BOX[2] <= lo <= KOREA_BOX[3]):
            continue
        d = None
        if near:
            d = math.hypot((la - near[0]) * 111320, (lo - near[1]) * 88800)
        out.append((c, la, lo, d))
    out.sort(key=lambda t: (t[3] if t[3] is not None else 0))
    return out


# ══════════════════════════════════════════════════════════════════
# 2. SHP / DBF 파서 (표준 라이브러리)
# ══════════════════════════════════════════════════════════════════

def _dec(b):
    for e in ("cp949", "utf-8"):
        try:
            return b.decode(e).strip()
        except UnicodeDecodeError:
            pass
    return b.decode("latin1").strip()


def read_dbf(data: bytes) -> list[dict]:
    nrec, hlen, rlen = struct.unpack("<IHH", data[4:12])
    flds, off = [], 32
    while data[off] != 0x0D:
        fd = data[off:off + 32]
        flds.append((fd[:11].split(b"\x00")[0].decode("ascii"), fd[16]))
        off += 32
    out, p = [], hlen
    for _ in range(nrec):
        raw = data[p:p + rlen]
        p += rlen
        if len(raw) < rlen or raw[:1] == b"\x1a":
            break
        o, r = 1, {}
        for nm, ln in flds:
            r[nm] = _dec(raw[o:o + ln])
            o += ln
        out.append(r)
    return out


def read_shp_polygons(data: bytes) -> list[list[list[float]]]:
    """레코드별 [ring, ring, ...], ring = [x0,y0,x1,y1,...] (평탄 배열, TM 그대로)."""
    if struct.unpack(">I", data[:4])[0] != 9994:
        raise ValueError("SHP 파일이 아님")
    out, p, end = [], 100, len(data)
    while p + 8 <= end:
        _, rl = struct.unpack(">II", data[p:p + 8])
        body = data[p + 8:p + 8 + rl * 2]
        p += 8 + rl * 2
        st, = struct.unpack("<I", body[:4])
        if st == 0:                       # Null shape
            out.append([])
            continue
        if st not in (5, 15, 25):         # Polygon / PolygonZ / PolygonM
            out.append([])
            continue
        nparts, npts = struct.unpack("<II", body[36:44])
        parts = struct.unpack(f"<{nparts}I", body[44:44 + nparts * 4])
        base = 44 + nparts * 4
        xy = struct.unpack(f"<{npts * 2}d", body[base:base + npts * 16])
        rings = []
        for i, s in enumerate(parts):
            e = parts[i + 1] if i + 1 < nparts else npts
            rings.append([round(v, 2) for v in xy[s * 2:e * 2]])
        out.append(rings)
    return out


# ══════════════════════════════════════════════════════════════════
# 3. 구역
# ══════════════════════════════════════════════════════════════════

# 레이어표_181.xlsx 기준 (원천 동봉 코드표)
KIND = {
    "UQ1100": ("도시개발구역", "기타"),
    "UQ1210": ("주거환경개선사업구역", "주거환경"),
    "UQ1211": ("주거환경개선사업", "주거환경"),
    "UQ1212": ("주거환경관리사업", "주거환경"),
    "UQ1220": ("재개발사업구역", "재개발"),
    "UQ1221": ("주택정비형 재개발구역", "재개발"),
    "UQ1222": ("도시정비형 재개발구역", "재개발"),
    "UQ1230": ("재개발사업지구", "재개발"),
    "UQ1231": ("주택정비형 재개발지구", "재개발"),
    "UQ1232": ("도시정비형 재개발지구", "재개발"),
    "UQ1240": ("재건축사업구역", "재건축"),
    "UQ1206": ("주택재건축사업", "재건축"),
    "UQ1250": ("결합정비구역", "기타정비"),
    "UQ1260": ("자율주택정비사업구역", "소규모"),
    "UQ1270": ("가로주택정비사업구역", "소규모"),
    "UQ1280": ("소규모재건축사업구역", "소규모"),
    "UQ1290": ("정비구역(도시및주거환경정비)", "기타정비"),
    "UQ1200": ("정비구역", "기타정비"),
    "UQ5100": ("재정비촉진지구", "촉진"),
    "UQ5110": ("주거지형재정비촉진지구", "촉진"),
    "UQ5120": ("중심지형재정비촉진지구", "촉진"),
    "UQ5130": ("고밀복합형재정비촉진지구", "촉진"),
    "UQ5140": ("존치정비구역", "촉진"),
    "UQ5150": ("존치관리구역", "촉진"),
}
# 우리가 판정에 쓰는 계열만 추출 (나머지 산업단지·보전산지 등은 버림)
KEEP = tuple(KIND)

SIGUNGU = {
    "11000": "서울특별시(시 결정)", "11110": "종로구", "11140": "중구", "11170": "용산구",
    "11200": "성동구", "11215": "광진구", "11230": "동대문구", "11260": "중랑구",
    "11290": "성북구", "11305": "강북구", "11320": "도봉구", "11350": "노원구",
    "11380": "은평구", "11410": "서대문구", "11440": "마포구", "11470": "양천구",
    "11500": "강서구", "11530": "구로구", "11545": "금천구", "11560": "영등포구",
    "11590": "동작구", "11620": "관악구", "11650": "서초구", "11680": "강남구",
    "11710": "송파구", "11740": "강동구",
}


@dataclass
class Zone:
    name: str
    code: str            # ATRB_SE (가장 구체적인 세분류)
    kind: str            # 사람이 읽는 이름
    family: str          # 재개발 / 재건축 / 촉진 / 소규모 / 주거환경 / 기타정비
    area: float          # DGM_AR (㎡) — 고시도형 실측
    sigungu: str
    notice: str          # 고시번호 (NTFC_SN)
    created: str         # YYYYMMDD
    agz: str = ""        # WTNNC_SN — 정보몽땅 구역 ID 와 같은 포맷(11000AGZ...).
                         # 지금은 정보몽땅 목록 페이징이 막혀 못 긁지만, 얻는 즉시 정확 조인용.
    rings: list = field(default_factory=list)   # TM 평탄배열
    bbox: tuple = (0, 0, 0, 0)
    parts: int = 1                              # 분리 조각 수 (고시 하나가 떨어진 여러 필지군)

    @property
    def gu(self) -> str:
        return SIGUNGU.get(self.sigungu, self.sigungu)

    @property
    def 촉진(self) -> bool:
        return self.family == "촉진"

    def center_wgs84(self):
        x = (self.bbox[0] + self.bbox[2]) / 2
        y = (self.bbox[1] + self.bbox[3]) / 2
        return tm_to_wgs84(x, y)

    def contains_tm(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        inside = False
        for r in self.rings:                 # 구멍(hole)은 XOR 로 자연 처리
            n = len(r) // 2
            j = n - 1
            for i in range(n):
                xi, yi = r[2 * i], r[2 * i + 1]
                xj, yj = r[2 * j], r[2 * j + 1]
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                    inside = not inside
                j = i
        return inside

    def contains(self, lat: float, lon: float) -> bool:
        return self.contains_tm(*wgs84_to_tm(lat, lon))


def _bbox(rings):
    xs = [v for r in rings for v in r[0::2]]
    ys = [v for r in rings for v in r[1::2]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


# ── SHP → 우리 포맷 ──

def build(src: str, out: str = ZONE_JSON) -> int:
    """의제처리구역 zip/디렉터리 → data/zones-seoul.json (정비 계열만)."""
    shp = dbf = None
    if src.lower().endswith(".zip"):
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                if n.lower().endswith(".shp"):
                    shp = z.read(n)
                elif n.lower().endswith(".dbf"):
                    dbf = z.read(n)
    else:
        for dirpath, _, files in os.walk(src):
            for n in files:
                p = os.path.join(dirpath, n)
                if n.lower().endswith(".shp"):
                    shp = open(p, "rb").read()
                elif n.lower().endswith(".dbf"):
                    dbf = open(p, "rb").read()
    if not shp or not dbf:
        raise SystemExit(f"{src} 안에서 .shp/.dbf 를 못 찾음")

    recs = read_dbf(dbf)
    geoms = read_shp_polygons(shp)
    if len(recs) != len(geoms):
        raise SystemExit(f"DBF {len(recs)} vs SHP {len(geoms)} 불일치")

    # ① 정비 계열만 추리고, 원천의 완전중복(같은 도형 이중등재)을 도형 해시로 제거
    raw, seen = [], set()
    for r, g in zip(recs, geoms):
        code = r.get("ATRB_SE") or r.get("SCLAS_CL") or r.get("MLSFC_CL") or r.get("LCLAS_CL")
        if code not in KIND or not g:
            continue
        rings = [[round(v) for v in ring] for ring in g]     # 1m 격자 (구역 판정엔 충분)
        h = (code, r.get("DGM_NM", "").strip(), r.get("NTFC_SN", ""), hash(tuple(map(tuple, rings))))
        if h in seen:
            continue
        seen.add(h)
        try:
            area = float(r.get("DGM_AR") or 0)
        except ValueError:
            area = 0.0
        raw.append({
            "name": r.get("DGM_NM", "").strip(), "code": code, "kind": KIND[code][0],
            "family": KIND[code][1], "area": area, "sigungu": r.get("SIGNGU_SE", ""),
            "notice": r.get("NTFC_SN", ""), "created": r.get("CREATE_DAT", ""),
            "agz": (r.get("WTNNC_SN") or "").strip(), "rings": rings,
        })

    # ② 같은 고시(이름+코드+고시번호)의 분리 조각은 한 구역으로 병합 — 면적은 합산이 고시 면적
    #    고시번호가 없으면 병합하지 않는다(다른 구역을 잘못 합칠 위험).
    merged, order = {}, []
    for z in raw:
        k = (z["name"], z["code"], z["notice"]) if z["notice"] else id(z)
        if k not in merged:
            merged[k] = dict(z, parts=1)
            order.append(k)
        else:
            m = merged[k]
            m["rings"].extend(z["rings"])
            m["area"] += z["area"]
            m["parts"] += 1

    zones = []
    for k in order:
        z = merged[k]
        z["area"] = round(z["area"], 2)
        z["bbox"] = [round(v) for v in _bbox(z["rings"])]
        zones.append(z)

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"출처": SRC_DOC, "좌표계": "EPSG:2097",
                   "원천레코드": len(recs), "정비계열": len(raw), "구역": len(zones),
                   "zones": zones}, fh, ensure_ascii=False, separators=(",", ":"))
    return len(zones)


# ── 원천 자동 수집 (서울 열린데이터광장) ──
DATASET = "OA-20957"
PAGE_URL = f"https://data.seoul.go.kr/dataList/{DATASET}/F/1/datasetView.do"
FILE_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"
# <span title="…zip" onclick="javascript:downloadFile('9');">  — 목록 1행이 최신
ZIP_ROW = re.compile(r'title="([^"]+\.zip)"[^>]*downloadFile\((?:&#39;|\')?(\d+)')


def fetch(dest: str = None) -> str:
    """열린데이터광장에서 최신 의제처리구역 zip 을 받아 경로를 돌려준다.

    포털이 파일목록을 seq 로만 노출해서, 데이터셋 페이지를 읽어 최신 항목을 고른다.
    (구조가 바뀌어 못 찾으면 사람이 받아 --setup 으로 넣으면 된다.)
    """
    req = urllib.request.Request(PAGE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", "replace")
    rows = re.findall(ZIP_ROW, page)
    if not rows:
        raise SystemExit(
            "포털 페이지에서 파일 목록을 못 찾음(구조 변경). 수동으로 받아서:\n"
            f"  {PAGE_URL}  →  python geo.py --setup <받은.zip>")
    name, seq = rows[0]                       # 목록 1행이 최신
    dest = dest or os.path.join(ROOT, "data", "_src-" + name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    body = urllib.parse.urlencode({"infId": DATASET, "seqNo": seq, "seq": seq,
                                   "infSeq": 1}).encode()
    req = urllib.request.Request(FILE_URL, data=body, headers={
        "User-Agent": "Mozilla/5.0", "Referer": PAGE_URL})
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()
    if blob[:2] != b"PK":
        raise SystemExit(f"zip 이 아닌 응답({len(blob)}B). 수동 다운로드 필요: {PAGE_URL}")
    with open(dest, "wb") as fh:
        fh.write(blob)
    return dest


_CACHE = None


def load(path: str = ZONE_JSON) -> list[Zone]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not os.path.exists(path):
        raise SystemExit(
            "정비구역 데이터가 없음.\n"
            "  1) 서울 열린데이터광장 OA-20957 '의제처리구역 위치정보' zip 다운로드\n"
            "  2) python geo.py --setup <받은.zip>")
    d = json.load(open(path, encoding="utf-8"))
    _CACHE = [Zone(z["name"], z["code"], z["kind"], z["family"], z["area"], z["sigungu"],
                   z["notice"], z["created"], z.get("agz", ""),
                   z["rings"], tuple(z["bbox"]), z.get("parts", 1)) for z in d["zones"]]
    return _CACHE


# ── 질의 ──

def at(lat: float, lon: float, zones=None) -> list[Zone]:
    """이 좌표를 품는 구역 전부 (정비구역 + 촉진지구가 겹칠 수 있음)."""
    x, y = wgs84_to_tm(lat, lon)
    return [z for z in (zones or load()) if z.contains_tm(x, y)]


def search(term: str, zones=None, sigungu: str = "") -> list[Zone]:
    out = [z for z in (zones or load()) if term in z.name]
    if sigungu:
        out = [z for z in out if z.sigungu == sigungu]
    return sorted(out, key=lambda z: -z.area)


def by_sigungu(code: str, zones=None) -> list[Zone]:
    return sorted([z for z in (zones or load()) if z.sigungu == code], key=lambda z: -z.area)


# ── criteria_engine 연결 ──

# 정비사업 본체로 볼 계열 (촉진지구는 상위 '지구'라 본체가 아님)
_BODY = ("재개발", "재건축", "주거환경", "소규모", "기타정비")


def to_area_fact(z: Zone):
    """구역 면적 → Fact(P1). 고시도형 실측이라 '정비계획 자료' 요청이 사라진다."""
    from criteria_engine import Fact, Grade
    return Fact(z.area, Grade.P1, SRC_DOC,
                f"{z.name} {z.kind} 고시도형 면적 {z.area:,.0f}㎡"
                + (f" (고시 {z.notice})" if z.notice else ""))


def pick(hits: list[Zone]) -> tuple[Optional[Zone], Optional[Zone]]:
    """적중 구역들 → (사업 본체 구역, 재정비촉진지구).

    한 점에 정비구역과 촉진지구가 겹쳐 잡히는 게 정상이다.
    본체가 여럿이면 가장 작은 것 = 가장 구체적인 지정으로 본다.
    """
    bodies = [z for z in hits if z.family in _BODY]
    promo = next((z for z in hits if z.family == "촉진"), None)
    body = min(bodies, key=lambda z: z.area) if bodies else None
    return body, promo


def to_criteria(hits: list[Zone]):
    """적중 구역 → criteria_engine.Area (지정고시·면적·사업유형·촉진 자동)."""
    from criteria_engine import Area, Fact, Grade
    body, promo = pick(hits)
    if body is None:
        # 촉진지구 안이지만 개별 구역은 아직 미지정 → 지정 아님, 촉진 기준(50%)만 적용
        return Area(재정비촉진지구=promo is not None)
    사업 = "재건축" if body.family == "재건축" else "재개발"
    라벨 = f"{body.name} ({body.kind})" + (f" · {promo.name} 안" if promo else "")
    return Area(
        사업유형=사업, 지역="서울", 재정비촉진지구=promo is not None,
        면적=to_area_fact(body),
        지정고시=Fact(라벨, Grade.P1, SRC_DOC,
                   f"{body.kind} 고시도형 등재"
                   + (f" · 고시번호 {body.notice}" if body.notice else "")
                   + (f" · 도형 {body.created}" if body.created else "")),
    )


def geojson(zones, path: str):
    feats = []
    for z in zones:
        rings = []
        for r in z.rings:
            rings.append([[round(lo, 6), round(la, 6)] for la, lo in
                          (tm_to_wgs84(r[i], r[i + 1]) for i in range(0, len(r), 2))])
        feats.append({"type": "Feature",
                      "properties": {"name": z.name, "kind": z.kind, "family": z.family,
                                     "area": z.area, "gu": z.gu, "notice": z.notice},
                      "geometry": {"type": "Polygon", "coordinates": rings}})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh, ensure_ascii=False)
    return len(feats)


# ── 출력 ──

def render(z: Zone, detail: bool = True) -> str:
    la, lo = z.center_wgs84()
    L = [f"■ {z.name}",
         f"  종류: {z.kind}  ({z.family})",
         f"  면적: {z.area:,.0f}㎡" + (f"  ≥ 1만㎡ ✓" if z.area >= 10000 else
                                    f"  (5천~1만㎡, 심의 완화 대상)" if z.area >= 5000 else "  < 5천㎡ ✗"),
         f"  자치구: {z.gu}" + (f"   (분리 {z.parts}개 조각 합산)" if z.parts > 1 else "")]
    if detail:
        L += [f"  고시번호: {z.notice or '—'}   도형생성 {z.created or '—'}",
              f"  중심좌표: {la:.6f}, {lo:.6f}",
              f"  출처: {SRC_DOC}"]
    return "\n".join(L)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="정비구역 경계 — 조회·판정")
    p.add_argument("--setup", help="의제처리구역 zip/디렉터리 → data/zones-seoul.json")
    p.add_argument("--fetch", action="store_true",
                   help="열린데이터광장에서 최신 원천 zip 을 받아 그대로 --setup 까지")
    p.add_argument("--search", help="구역명 검색")
    p.add_argument("--sigungu", help="자치구 코드 (11620=관악구)")
    p.add_argument("--at", nargs=2, type=float, metavar=("LAT", "LON"), help="이 좌표가 어느 구역 안인가")
    p.add_argument("--geojson", help="지도용 WGS84 GeoJSON 내보내기 (--search/--sigungu 와 함께)")
    p.add_argument("--top", type=int, default=20)
    a = p.parse_args(argv)

    if a.fetch:
        z = fetch()
        print(f"받음: {z}  ({os.path.getsize(z)/1e6:.1f}MB)")
        a.setup = a.setup or z
    if a.setup:
        n = build(a.setup)
        print(f"저장: {ZONE_JSON}  (구역 {n}건)")
        return

    zones = load()
    if a.at:
        hits = at(a.at[0], a.at[1], zones)
        if not hits:
            print(f"■ {a.at[0]:.6f}, {a.at[1]:.6f}")
            print("  ⚪ 지정된 정비구역·촉진지구 안이 아님")
            print("     (아직 지정 전일 수 있음 — 요건 판정은 aging.py 로)")
            return
        print(f"■ {a.at[0]:.6f}, {a.at[1]:.6f} — 구역 {len(hits)}건 적중\n")
        for z in hits:
            print(render(z))
            print()
        return

    sel = search(a.search, zones, a.sigungu or "") if a.search else \
        by_sigungu(a.sigungu, zones) if a.sigungu else zones
    if a.geojson:
        n = geojson(sel, a.geojson)
        print(f"저장: {a.geojson}  ({n}건, WGS84)")
        return
    print(f"■ {len(sel)}건" + (f" (상위 {a.top} 표시)" if len(sel) > a.top else "") + "\n")
    for z in sel[:a.top]:
        print(f"  {z.family:<5} {z.area:>12,.0f}㎡  {z.gu:<8} {z.name}")


if __name__ == "__main__":
    main()
