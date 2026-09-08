"""정보몽땅 사업장 목록 → districts.csv

같은 사이트를 두 번 긁지 않는다. 루트의 `stage.py` 가 이미 목록을 받아
`data/stages-seoul.json` 에 두고 있고(cafe id·구역 ID 포함), 여기서는 그걸
수집물 형식(CSV)으로 내보내기만 한다. `--refetch` 를 주면 stage 가 다시 받는다.
"""
import csv
import os
import sys
from datetime import date

from . import paths

COLS = ["자치구", "사업구분", "법", "사업장명", "대표지번", "진행단계", "단계순위",
        "운영구분", "운영단계", "법정동코드", "PNU", "cafeId", "recordCode"]


def build(refetch: bool = False, sigungu: str = None) -> str:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import stage

    if refetch:
        rows = stage.fetch_list()
        stage.build(rows_in=rows, 원천="사업장검색 목록(collect --refetch)")
        stage._CACHE = None
    sites = stage.load()
    if sigungu:
        sites = [s for s in sites if s.bjd[:5] == sigungu]

    os.makedirs(paths.CLEANUP, exist_ok=True)
    out = os.path.join(paths.CLEANUP, "districts.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        for s in sites:
            w.writerow([s.gu, s.kind, s.law, s.name, s.jibun, s.stage, s.rank,
                        s.op, s.op_stage, s.bjd, s.pnu, s.cafe, s.agz])
    with open(os.path.join(paths.CLEANUP, "districts.meta"), "w", encoding="utf-8") as fh:
        fh.write(f"수집일\t{date.today()}\n건수\t{len(sites)}\n"
                 f"출처\t정비사업 정보몽땅 사업장검색 (stage.py 경유)\n")
    return f"{out}  ({len(sites):,}건)"
