"""수집 리포트 — 무엇을 얼마나 모았고 어디서 막혔는지."""
import csv
import glob
import os
from collections import Counter
from datetime import date

from . import paths


def build() -> str:
    L = [f"# 수집 리포트 — {date.today()}", ""]

    # 정보몽땅
    dp = os.path.join(paths.CLEANUP, "districts.csv")
    if os.path.exists(dp):
        rows = list(csv.DictReader(open(dp, encoding="utf-8-sig")))
        gu = Counter(r["자치구"] for r in rows)
        L += ["## 정보몽땅 사업장", "",
              f"- {len(rows):,}건 · 자치구 {len(gu)}개",
              f"- 상위: " + ", ".join(f"{k} {v}" for k, v in gu.most_common(5)), ""]

    # 구청별
    L += ["## 구청 고시공고", "",
          "| 구청 | 고시 | 첨부 | 확장자 | 텍스트 추출 | 비고 |",
          "|---|---|---|---|---|---|"]
    for g in sorted(os.listdir(paths.NOTICES)) if os.path.isdir(paths.NOTICES) else []:
        ip = os.path.join(paths.NOTICES, g, "index.csv")
        if not os.path.exists(ip):
            continue
        rows = list(csv.DictReader(open(ip, encoding="utf-8-sig")))
        notices = len({r["게시번호"] for r in rows})
        atts = [r for r in rows if r["첨부URL"]]
        ext = Counter(r["확장자"] for r in atts if r["확장자"])
        txt = len(glob.glob(os.path.join(paths.NOTICES, g, "text", "*.txt")))
        bad = [r for r in atts if r["비고"] and "캐시" not in r["비고"]]
        L.append(f"| {g} | {notices} | {len(atts)} | "
                 f"{', '.join(f'{k} {v}' for k, v in ext.most_common()) or '—'} | "
                 f"{txt}/{len(atts)} | {('문제 ' + str(len(bad))) if bad else '—'} |")
        kw = Counter()
        for r in rows:
            for k in (r["검색어"] or "").split("|"):
                if k:
                    kw[k] += 1
        if kw:
            L += ["", f"  검색어 히트({g}): " +
                  ", ".join(f"{k} {v}" for k, v in kw.most_common()), ""]

    # 매칭
    mp = os.path.join(paths.CLEANUP, "match.csv")
    if os.path.exists(mp):
        m = list(csv.DictReader(open(mp, encoding="utf-8-sig")))
        up = os.path.join(paths.CLEANUP, "match_unmatched.csv")
        u = list(csv.DictReader(open(up, encoding="utf-8-sig"))) if os.path.exists(up) else []
        tot = len(m) + len(u)
        L += ["", "## 정보몽땅 매칭", "",
              f"- 매칭 {len(m)} / 미매칭 {len(u)} — {len(m)/tot:.0%}" if tot else "- 대상 없음",
              "- 규율: 번호가 어긋나면 붙이지 않는다(미매칭으로 남긴다)", ""]
        by = Counter(r["매칭방식"] for r in m)
        if by:
            L.append("  방식: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))

    # eminwon 호스트 생존
    hp = os.path.join(paths.ROOT, "config_eminwon_hosts.json")
    if os.path.exists(hp):
        import json
        d = json.load(open(hp, encoding="utf-8"))
        f, m = d["found"], d["missed"]
        L += ["", "## eminwon 호스트 (메타데이터 경로)", "",
              f"- 생존 {len(f)} / 실패 {len(m)}",
              "- 호스트 명명이 두 갈래다: `eminwon.{en}.go.kr` / `{en}.eminwon.seoul.kr`",
              "", "| 자치구 | 호스트 | 확인 |", "|---|---|---|"]
        for gu, v in sorted(f.items()):
            L.append(f"| {gu} | `{v['host']}` | {v['note']} |")
        if m:
            L += ["", f"실패: {', '.join(sorted(m))} — 영문 약어가 다르거나 외부 미노출", ""]
        L += ["",
              "⚠ **eminwon 으로는 첨부를 받을 수 없다.** 상세의 `goDownLoad()` 인자가 Base64 로",
              "암호화돼 있고 세션 쿠키를 유지해도 `FileDown.jsp` 가 133바이트 오류 HTML 을 준다(실측).",
              "평문 인자는 구청 CMS 상세에만 노출된다 → 첨부가 필요하면 CMS 경로가 있어야 한다.", ""]

    # 고시문에서 뭐가 나오는지 (다음 단계 VLM 이 고를 대상)
    import re as _re
    import glob as _g
    tot = {"면적": 0, "호수밀도": 0, "접도율": 0, "노후도": 0, "지정·결정": 0, "n": 0}
    pat = {"면적": r"[\d,]{3,}\s*㎡", "호수밀도": r"호수밀도", "접도율": r"접도율",
           "노후도": r"노후[^\n]{0,50}?\d{1,3}(?:\.\d+)?\s*%",
           "지정·결정": r"정비구역\s*(?:지정|결정)"}
    for g in sorted(os.listdir(paths.NOTICES)) if os.path.isdir(paths.NOTICES) else []:
        for f in _g.glob(os.path.join(paths.NOTICES, g, "text", "*.txt")):
            t = open(f, encoding="utf-8", errors="replace").read()
            tot["n"] += 1
            for k, p_ in pat.items():
                if _re.search(p_, t):
                    tot[k] += 1
    if tot["n"]:
        L += ["", "## 고시문에서 읽히는 것 (규칙 기반, VLM 전 단계)", "",
              f"텍스트 {tot['n']}건 중 —", ""]
        for k in ("지정·결정", "면적", "호수밀도", "접도율", "노후도"):
            L.append(f"- {k}: {tot[k]}건")
        L += ["",
              "구역 제원(호수밀도·접도율)은 소수의 '정비계획 결정 고시' 에만 있고 대부분 표 안에 있다.",
              "표 구조를 이해해야 값과 라벨이 이어지므로 여기서부터가 VLM 몫이다.", ""]

    L += ["", "## 해제 이력 — 구청 고시에는 없다", "",
          "정비구역 해제 고시를 찾으려 했으나 관악구 고시공고(eminwon 전 기간 검색 포함)에",
          "정비구역 해제 고시가 없다. '해제' 로 걸린 것은 산사태취약지역·코로나 행정명령·",
          "시설물 지정해제·거푸집 해제·평가위원 위촉해제뿐이었다.",
          "",
          "이유는 조문에 있다 — 도시정비법 §8① 정비구역의 **지정권자는 특별시장**이고,",
          "§20① 해제도 지정권자가 한다. 서울에서 자치구는 입안권자라 구청 고시에는",
          "정비계획 입안 공람공고가 주로 올라온다.",
          "",
          "→ 해제 이력은 **서울시 고시**에서 찾아야 한다(다음 조사 대상).", ""]

    L += ["", "## 접근 제약", "",
          "| 자치구 | CMS(첨부 가능) | eminwon(메타만) | 메모 |", "|---|---|---|---|",
          "| 관악구 | 가능 | 가능 | robots 허용 |",
          "| 동작구 | **불가** | 가능 | 포털 robots 가 `/portal/bbs/` 차단 |",
          "| 영등포구 | **불가** | 가능 | 포털 robots 가 `Disallow: /` (사이트 전체) |",
          "",
          "동작·영등포는 포털이 게시판 크롤링을 막았다. eminwon 호스트에는 robots 가 없어",
          "기술적으로는 접근되지만, 같은 기관이 운영하는 다른 문이다 — 첨부 수집은 하지 않았다.", ""]

    os.makedirs(paths.REPORT, exist_ok=True)
    out = os.path.join(paths.REPORT, "collect.md")
    open(out, "w", encoding="utf-8").write("\n".join(L))
    return out
