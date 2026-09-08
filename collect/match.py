"""고시문 ↔ 정보몽땅 사업장 매칭.

루트의 `stage._tokens` 를 그대로 쓴다 — 구역 식별자는 '신림7' 처럼 [지역명+번호] 이고,
그 규칙은 이미 검증돼 있다(서울 144건 중 130건 이름 일치, 오매칭 0).
여기서도 규율은 같다: **번호가 어긋나면 붙이지 않는다.**
"""
import csv
import os
import re
import sys

from . import paths


def _stage():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import stage
    return stage


def _dong(s: str) -> set:
    return set(re.findall(r"([가-힣]{2,4}동)", s or ""))


def run(gu: str = None) -> dict:
    st = _stage()
    sites = st.load()
    idxs = []
    root = paths.NOTICES
    for g in ([gu] if gu else sorted(os.listdir(root)) if os.path.isdir(root) else []):
        p = os.path.join(root, g, "index.csv")
        if os.path.exists(p):
            idxs.append((g, p))

    matched, unmatched = [], []
    seen = set()
    for g, p in idxs:
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            key = (g, r["게시번호"])
            if key in seen:
                continue
            seen.add(key)
            title = r["제목"]
            tok = st._tokens(title)
            dong = _dong(title)
            cands = [s for s in sites if s.gu == g]
            hit = [s for s in cands if tok & st._tokens(s.name)]
            how, conf = "토큰", 1.0
            if not hit and dong:
                # 번호를 못 뽑으면 동 이름으로 좁히되 신뢰도를 낮춘다
                hit = [s for s in cands if dong & _dong(s.jibun)]
                how, conf = "동이름", 0.4
            if len(hit) == 1:
                s = hit[0]
                matched.append([g, r["게시번호"], title, s.name, s.stage, s.agz,
                                s.pnu, how, conf])
            elif len(hit) > 1 and how == "토큰":
                s = max(hit, key=lambda x: x.rank)
                matched.append([g, r["게시번호"], title, s.name, s.stage, s.agz,
                                s.pnu, "토큰(다수)", 0.7])
            else:
                unmatched.append([g, r["게시번호"], title,
                                  "후보 없음" if not hit else f"후보 {len(hit)}건 — 확정 불가"])

    os.makedirs(paths.CLEANUP, exist_ok=True)
    mp = os.path.join(paths.CLEANUP, "match.csv")
    up = os.path.join(paths.CLEANUP, "match_unmatched.csv")
    with open(mp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["구청", "게시번호", "고시제목", "사업장명", "진행단계",
                    "recordCode", "PNU", "매칭방식", "신뢰도"])
        w.writerows(matched)
    with open(up, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["구청", "게시번호", "고시제목", "사유"])
        w.writerows(unmatched)
    tot = len(matched) + len(unmatched)
    return {"matched": len(matched), "unmatched": len(unmatched),
            "rate": (len(matched) / tot if tot else 0), "files": (mp, up)}
