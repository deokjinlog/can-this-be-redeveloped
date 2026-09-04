"""
elapse.py — 정비사업 추진경과(인가 일자) 수집

`stage.py` 는 "지금 어느 단계인가"까지만 안다. 재건축 3년 트리(시행령 §37② 예외5~7)는
**날짜**가 있어야 돌아간다:

    예외5  조합설립인가일   + 3년 내 사업시행계획인가 미신청
    예외6  사업시행인가일   + 3년 내 미착공
    예외7  착공일           + 3년 내 미준공

정보몽땅 조합 카페의 '추진경과' 화면에 단계별 일자가 공개돼 있어 그걸 읽는다.

    사업장 목록 ─cafe id─→ /cafe/mainIndx.do?cafeUrl=<id>      (cafeId·bsnsPk 획득)
                          └→ /cafe/mainIndx/cleanup-prtnelapse/vscr.do   (추진경과)

⚠ 근거등급 S1(기관 게시). 관보 고시 원문이 아니라 조합이 올린 화면값이다.
⚠ (변경)인가가 여러 번이면 **최초 인가일**을 기산점으로 본다(3년 기간의 시작).
⚠ 공개하지 않은 조합도 있다 — 없으면 없는 대로 두고 추측하지 않는다.

    python elapse.py --gu 관악구          # 그 자치구 전체 수집 (요청 사이 간격 둠)
    python elapse.py --cafe gaepo3        # 한 곳만
    python elapse.py --show 신림1          # 수집된 것 보기
표준 라이브러리만 사용.
"""

import argparse
import html
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data", "elapse.json")
BASE = "https://cleanup.seoul.go.kr"
MAIN = BASE + "/cafe/mainIndx.do?cafeUrl={}"
PRTN = BASE + "/cafe/mainIndx/cleanup-prtnelapse/vscr.do?cafeId={}&bsnsPk={}"
SRC_DOC = "정비사업 정보몽땅 조합 추진경과(기관 게시)"
UA = {"User-Agent": "Mozilla/5.0 (can-this-be-redeveloped; personal non-commercial)"}

# 추진경과 단계명 → C모듈이 쓰는 기산점
#   값: (Case 필드명, 그 단계에서 '인가'로 볼 이벤트 키워드)
ANCHOR = {
    "조합설립인가": ("조합설립인가일", ("인가",)),
    "사업시행인가": ("사업시행계획인가일", ("인가",)),
    "착공신고": ("착공일", ("착공",)),
    "준공인가": ("준공일", ("인가",)),
    "관리처분인가": ("관리처분인가일", ("인가",)),
}
# 신청/고시는 기산점이 아니다 — '인가' 자체만 본다
_NOT_ANCHOR = ("신청", "고시", "총회", "공람", "심의")

_SPLIT = '<li class="foldings-li'      # 중첩 <li> 가 있어 정규식보다 분할이 안전하다
_NAME = re.compile(r"<span>\s*(.*?)\s*</span>", re.S)
_EV = re.compile(r'<li>\s*<h3 class="tit">\s*(.*?)\s*</h3>\s*(.*?)</li>', re.S)
_DATE = re.compile(r"(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})")


@dataclass
class Elapse:
    cafe: str
    name: str = ""
    cafe_id: str = ""
    bsns_pk: str = ""
    events: list = field(default_factory=list)   # [[단계, 날짜, 이벤트명, 메모], ...]

    def first(self, 단계: str, keys=("인가",)) -> Optional[str]:
        """그 단계의 **최초** 인가일 (변경 인가가 여러 번이면 처음 것)."""
        hits = [d for st, d, ev, _ in self.events
                if st == 단계 and any(k in ev for k in keys)
                and not any(x in ev for x in _NOT_ANCHOR)]
        return min(hits) if hits else None

    @property
    def anchors(self) -> dict:
        out = {}
        for 단계, (fld, keys) in ANCHOR.items():
            d = self.first(단계, keys)
            if d:
                out[fld] = d
        return out

    def has(self, 단계: str) -> bool:
        return any(st == 단계 for st, *_ in self.events)


def _get(url: str, referer: str = None) -> str:
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _txt(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()


def fetch(cafe: str) -> Optional[Elapse]:
    """카페 id → 추진경과. 못 읽으면 None(추측하지 않는다)."""
    main = _get(MAIN.format(cafe))
    m = re.search(r"cleanup-prtnelapse/vscr\.do\?cafeId=([^&\"]+)&(?:amp;)?bsnsPk=([^&\"]+)", main)
    if not m:
        return None
    cafe_id, bsns_pk = m.group(1), m.group(2)
    title = re.search(r"<title>(.*?)</title>", main, re.S)
    e = Elapse(cafe, _txt(title.group(1)) if title else "", cafe_id, bsns_pk)
    page = _get(PRTN.format(cafe_id, bsns_pk), referer=MAIN.format(cafe))
    for blk in page.split(_SPLIT)[1:]:
        nm = _NAME.search(blk)
        if not nm:
            continue
        단계 = _txt(nm.group(1))
        for d_raw, body in _EV.findall(blk):
            dm = _DATE.search(re.sub(r"\s+", "", d_raw))
            if not dm:
                continue
            date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
            b = _txt(body)
            ev = re.match(r"\[(.*?)\]", b)
            e.events.append([단계, date, ev.group(1) if ev else "", b[:120]])
    return e if e.events else None


# ── 저장/적재 ──

def load(path: str = OUT) -> dict:
    if not os.path.exists(path):
        return {}
    d = json.load(open(path, encoding="utf-8"))
    return {k: Elapse(k, v["name"], v["cafe_id"], v["bsns_pk"], v["events"])
            for k, v in d.get("cafes", {}).items()}


def save(cafes: dict, path: str = OUT):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"출처": SRC_DOC, "수집": len(cafes),
                   "cafes": {k: {"name": v.name, "cafe_id": v.cafe_id,
                                 "bsns_pk": v.bsns_pk, "events": v.events}
                             for k, v in cafes.items()}},
                  fh, ensure_ascii=False, separators=(",", ":"))


def collect(cafes: list, delay: float = 0.8, path: str = OUT) -> dict:
    """카페 id 목록을 순서대로 수집. 이미 받은 건 건너뛴다."""
    have = load(path)
    todo = [c for c in cafes if c and c not in have]
    for i, c in enumerate(todo, 1):
        try:
            e = fetch(c)
        except Exception as ex:
            print(f"  [{i}/{len(todo)}] {c}: 실패 {type(ex).__name__}")
            time.sleep(delay)
            continue
        if e:
            have[c] = e
            print(f"  [{i}/{len(todo)}] {c}: {len(e.events)}건 · {', '.join(e.anchors) or '기산점 없음'}")
        else:
            print(f"  [{i}/{len(todo)}] {c}: 추진경과 미공개")
        time.sleep(delay)
    save(have, path)
    return have


# ── engine 연결 ──

# Case 필드 → 그 사건이 일어났다고 볼 수 있는 최소 진행단계 순위(stage.ORDER 기준)
_NEEDS_RANK = {
    "조합설립인가일": 60,
    "사업시행계획인가일": 70,
    "관리처분인가일": 80,
    "착공일": 90,
    "준공": 100,
}


def to_case_facts(e: Elapse, site=None) -> dict:
    """추진경과 → engine.Case 에 넣을 Fact 들 (S1).

    engine.Case.준공 은 날짜가 아니라 bool(준공했는가) 이라 따로 변환한다.

    site(stage.Site)를 주면 **아직 오지 않은 단계**를 Fact(None) 으로 채운다.
    '미확인' 과 '확정적으로 아직 없음' 은 다르다 — 전자는 자료를 물어야 하지만
    후자는 이미 답이다(착공 전인 구역에 착공일을 물을 이유가 없다).
    단계는 기관 게시치(S1)라 시차가 있을 수 있어, 근거 문구에 현재 단계를 남긴다.
    """
    from datetime import date as D
    from engine import Fact, Grade
    out = {}
    for fld, ds in e.anchors.items():
        y, m, d = (int(x) for x in ds.split("-"))
        span = f"{e.name or e.cafe} {fld} {ds} (최초 인가 기준)"
        if fld == "준공일":
            out["준공"] = Fact(True, Grade.S1, SRC_DOC, span)
        else:
            out[fld] = Fact(D(y, m, d), Grade.S1, SRC_DOC, span)

    if site is not None and getattr(site, "rank", -1) >= 0:
        for fld, need in _NEEDS_RANK.items():
            if fld in out:
                continue
            if site.rank < need:
                out[fld] = Fact(None, Grade.S1, "정보몽땅 진행단계",
                                f"현재 '{site.stage}' — 해당 단계 미도래")
    return out


def render(e: Elapse) -> str:
    L = [f"■ {e.name or e.cafe}   [{e.cafe}]",
         f"  추진경과 {len(e.events)}건 · 기산점 {len(e.anchors)}개"]
    for fld, d in e.anchors.items():
        L.append(f"    · {fld:<16} {d}")
    seen = []
    for st, d, ev, _ in e.events:
        if st not in seen:
            seen.append(st)
    L.append(f"  단계: {' → '.join(seen)}")
    L.append(f"  출처: {SRC_DOC} (S1) — 관보 고시 원문 아님")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description="정비사업 추진경과(인가 일자)")
    p.add_argument("--gu", help="자치구 전체 수집 (예: 관악구)")
    p.add_argument("--cafe", help="카페 id 하나만")
    p.add_argument("--show", help="수집된 것에서 이름으로 찾기")
    p.add_argument("--delay", type=float, default=0.8, help="요청 간격(초)")
    a = p.parse_args(argv)

    if a.show:
        have = load()
        hits = [e for e in have.values() if a.show in (e.name or "") or a.show in e.cafe]
        if not hits:
            raise SystemExit(f"'{a.show}' 없음 — 먼저 --gu 로 수집하세요")
        for e in hits[:5]:
            print(render(e))
            print()
        return

    if a.cafe:
        e = fetch(a.cafe)
        if not e:
            raise SystemExit("추진경과를 못 읽음")
        have = load()
        have[a.cafe] = e
        save(have)
        print(render(e))
        return

    if a.gu:
        import stage
        sites = [s for s in stage.load() if s.gu == a.gu]
        cafes = [s.cafe for s in sites if s.cafe]
        if not cafes:
            raise SystemExit(f"{a.gu} 사업장에 카페 id 가 없음 — stage.py --setup 를 최신 목록으로 다시")
        print(f"■ {a.gu} {len(sites)}개소 중 카페 {len(cafes)}건 수집 (간격 {a.delay}s)")
        have = collect(cafes, a.delay)
        got = [c for c in cafes if c in have]
        print(f"\n수집 완료 {len(got)}/{len(cafes)} → {OUT}")
        return

    have = load()
    print(f"수집된 추진경과 {len(have)}건. --gu / --cafe / --show 로 다루세요.")


if __name__ == "__main__":
    main()
