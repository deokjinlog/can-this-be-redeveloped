"""eminwon 호스트 탐색 — 어느 구가 원천에 직접 열려 있나.

호스트 명명이 두 갈래다(실측):
    eminwon.{en}.go.kr        관악·영등포
    {en}.eminwon.seoul.kr     동작
영문 약어가 구마다 제각각이라(ydp·sdm·ddm…) 후보를 여러 개 두고 순서대로 찔러본다.
목록 URL 이 200 + 고시 행을 돌려주면 생존으로 본다 — 200 만 보면 오류 페이지에 속는다.
"""
import json
import os
import re
import urllib.error

from . import http, paths
from .adapters import eminwon

PATTERNS = ("eminwon.{en}.go.kr", "{en}.eminwon.seoul.kr")
_ROWS = re.compile(r"searchDetail\('(\d+)'\)")


def candidates(en: str, alt=()) -> list:
    out = []
    for e in [en, *alt]:
        if not e:
            continue
        out += [p.format(en=e) for p in PATTERNS]
    return list(dict.fromkeys(out))


def probe(gu: str, host: str, query: str = "정비구역") -> tuple[bool, str]:
    ad = eminwon(gu, host)
    try:
        html = http.get(ad.list_page(1, query), encoding="utf-8", cache=False)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__
    ids = set(_ROWS.findall(html))
    if ids:
        return True, f"고시 {len(ids)}건"
    if "OfrNotAncmt" in html or "고시공고" in html:
        return True, "응답은 정상, 검색 결과 0"
    return False, "목록 아님"


def scan(only=None) -> dict:
    from .config import sigungu
    found, missed = {}, {}
    for code, info in sigungu().items():
        gu = info["name"]
        if only and gu not in only:
            continue
        hit = None
        tried = []
        for h in candidates(info.get("en", ""), info.get("alt", [])):
            ok, why = probe(gu, h)
            tried.append(f"{h} → {why}")
            if ok:
                hit = (h, why)
                break
        if hit:
            found[gu] = {"host": hit[0], "note": hit[1], "code": code}
        else:
            missed[gu] = tried
    out = os.path.join(paths.ROOT, "config_eminwon_hosts.json")
    os.makedirs(paths.ROOT, exist_ok=True)
    json.dump({"found": found, "missed": missed}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # 리포에도 남긴다 — 다음 실행이 다시 훑지 않도록
    repo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "eminwon_hosts.yaml")
    with open(repo, "w", encoding="utf-8") as fh:
        fh.write("# eminwon 호스트 생존 확인 결과 (collect hosts 가 갱신)\n")
        fh.write("# 첨부는 이 경로로 못 받는다 — 메타데이터 전용.\n\n")
        for gu, v in sorted(found.items()):
            fh.write(f'{gu}: {v["host"]}    # {v["note"]}\n')
        for gu in sorted(missed):
            fh.write(f'#{gu}: (못 찾음)\n')
    return {"found": found, "missed": missed, "file": out}
