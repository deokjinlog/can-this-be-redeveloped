"""
law.py — 법령 원문 수집·인용 (국가법령정보센터 OpenAPI)

이 프로젝트의 기준은 전부 조문에서 온다. 그런데 지금까지 조문은 '기억'으로 인용돼 있었고,
그래서 **인용이 틀린 곳이 있었다**(3년 트리를 §37② 로 적었는데 실제는 §37③1~3호).
원문을 받아 저장하고, 판정 근거에 그 원문을 그대로 붙인다.

    python law.py --fetch          # 도시정비법·시행령 원문 → data/law.json
    python law.py --show 39        # 법률 제39조
    python law.py --show 시행령37   # 시행령 제37조

출처: 국가법령정보센터 OpenAPI (law.go.kr/DRF). 공개 테스트 계정(OC=test) 사용 —
      받은 원문은 data/law.json 에 캐시하고 재요청하지 않는다.
표준 라이브러리만 사용.
"""

import argparse
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data", "law.json")
API = "https://www.law.go.kr/DRF/lawService.do?OC=test&target=law&MST={}&type=XML"
UA = {"User-Agent": "Mozilla/5.0 (can-this-be-redeveloped; personal non-commercial)"}

TARGETS = {
    "법": ("284065", "도시 및 주거환경정비법"),
    "령": ("287285", "도시 및 주거환경정비법 시행령"),
}


def _sp(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())


def fetch(mst: str) -> dict:
    req = urllib.request.Request(API.format(mst), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        root = ET.fromstring(r.read())
    out = {"법령명": root.findtext(".//법령명_한글"),
           "시행일자": root.findtext(".//시행일자"),
           "공포일자": root.findtext(".//공포일자"),
           "공포번호": root.findtext(".//공포번호"),
           "조문": {}}
    for u in root.findall(".//조문단위"):
        num = (u.findtext("조문번호") or "").strip()
        if not num:
            continue
        art = {"제목": _sp(u.findtext("조문제목")), "본문": _sp(u.findtext("조문내용")),
               "항": []}
        for h in u.findall("항"):
            hc = _sp(h.findtext("항내용"))
            if not hc:
                continue
            항 = {"내용": hc, "호": []}
            for ho in h.findall("호"):
                항["호"].append(_sp(ho.findtext("호내용")))
            art["항"].append(항)
        out["조문"][num] = art
    return out


def build(out_path: str = OUT) -> str:
    data = {"출처": "국가법령정보센터 OpenAPI (law.go.kr/DRF)", "법령": {}}
    for k, (mst, nm) in TARGETS.items():
        d = fetch(mst)
        data["법령"][k] = d
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    return " · ".join(f"{v['법령명']}({v['시행일자']} 시행, 조문 {len(v['조문'])})"
                      for v in data["법령"].values())


_CACHE = None


def load(path: str = OUT) -> dict:
    global _CACHE
    if _CACHE is None:
        if not os.path.exists(path):
            raise SystemExit("법령 원문이 없음.  python law.py --fetch")
        _CACHE = json.load(open(path, encoding="utf-8"))
    return _CACHE


def article(which: str, num: str) -> Optional[dict]:
    """which: '법' | '령' · num: '39' 같은 조 번호."""
    return load()["법령"].get(which, {}).get("조문", {}).get(num)


def cite(which: str, num: str, 항: int = None, 호: int = None) -> str:
    """판정 근거에 붙일 **원문 인용**. 조·항·호를 정확히 짚는다."""
    a = article(which, num)
    if not a:
        return ""
    if 항 is None:
        return a["본문"]
    try:
        h = a["항"][항 - 1]
    except IndexError:
        return ""
    if 호 is None:
        return h["내용"]
    try:
        return h["호"][호 - 1]
    except IndexError:
        return ""


def label(which: str, num: str, 항: int = None, 호: int = None) -> str:
    nm = "도시정비법" if which == "법" else "시행령"
    s = f"{nm} §{num}"
    if 항:
        s += "①②③④⑤⑥⑦⑧⑨"[항 - 1]
    if 호:
        s += f"{호}호"
    return s


def meta() -> str:
    d = load()["법령"]
    return " · ".join(f"{v['법령명']} {v['시행일자']} 시행" for v in d.values())


def main(argv=None):
    p = argparse.ArgumentParser(description="법령 원문")
    p.add_argument("--fetch", action="store_true", help="원문 받아 data/law.json 저장")
    p.add_argument("--show", help="조문 보기 (예: 39 / 시행령37)")
    a = p.parse_args(argv)
    if a.fetch:
        print("저장:", build())
        return
    if a.show:
        which = "령" if a.show.startswith("시행령") else "법"
        num = a.show.replace("시행령", "").strip()
        art = article(which, num)
        if not art:
            raise SystemExit(f"제{num}조 없음")
        print(f"■ {load()['법령'][which]['법령명']} 제{num}조 {art['제목']}")
        for i, h in enumerate(art["항"], 1):
            print("\n" + h["내용"])
            for j, ho in enumerate(h["호"], 1):
                print("   " + ho)
        return
    print(meta())


if __name__ == "__main__":
    main()
