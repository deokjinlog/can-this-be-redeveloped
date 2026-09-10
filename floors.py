"""
floors.py — 건축물대장 층별개요 → 반지하 주거 판정

영 별표1 제2호아목은 선택요건 하나를 이렇게 정한다.

    「건축법」 제2조제1항제5호에 따른 지하층의 전부 또는 일부를 주거용도로
    사용하는 건축물의 수가 해당 지역 전체 건축물의 수의 2분의 1 이상인 지역

표제부에는 '지하층수' 만 있고 그 층을 뭐로 쓰는지가 없다. 관악구 표제부에서
지하층을 가진 건축물이 79.7% 인데, 대부분 주차장·창고라 그 숫자를 반지하로
읽으면 요건을 통째로 오판한다. 그래서 층별개요를 따로 받는다.

    python floors.py --zone 신림7      # 구역 안 필지만 조회 → data/floors-<코드>.json
    python floors.py --stat            # 캐시 통계

조회 단위는 지번(필지)이고 한 지번에서 여러 동이 함께 온다. 동 귀속은
mgmBldrgstPk ↔ 표제부 '관리건축물대장PK' 로 붙인다(API 는 int, CSV 는 str).

출처: 국토교통부 건축물대장 서비스(apis.data.go.kr/1613000/BldRgstHubService).
      키는 .env 의 DATA_GO_KR_KEY — 커밋 금지.
표준 라이브러리만 사용.
"""

import argparse
import json
import os
import time
import urllib.request
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
API = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrFlrOulnInfo"
DELAY = 0.25          # 공공 API 예의 — 한 번에 몰아치지 않는다
SRC_DOC = "건축물대장 층별개요(국토교통부 공공데이터)"

# 지하층의 '실제' 용도는 기타용도(etcPurps) 에 있다. 주용도(mainPurpsCdNm)는
# 건물 전체 값이라 지하 주차장도 '연립주택' 으로 찍힌다 — 쓰면 안 된다.
RESI = ("주택", "주거", "아파트", "연립", "다세대", "다가구", "단독", "기숙사")
NONRESI = ("주차", "창고", "기계", "전기", "보일러", "대피", "방재", "저수", "펌프",
           "물탱크", "계단", "승강", "소매", "근린", "사무", "점포", "상가", "공장",
           "식당", "학원", "교회", "경비", "관리", "휴게", "office", "탈의")


def _load_env():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


def classify(use: str) -> Optional[bool]:
    """지하층 한 칸의 용도 → 주거 여부. None = 판정 불가(미상).

    요건이 '전부 또는 **일부**' 라서, 주거와 비주거가 같이 적혀 있으면 주거로 본다.
    """
    u = (use or "").strip()
    if not u:
        return None                       # 비어 있으면 지어내지 않는다
    if any(k in u for k in RESI):
        return True
    if any(k in u for k in NONRESI):
        return False
    return None                           # 처음 보는 용도어 — 미상으로 남긴다


def _fetch(sigungu: str, bjdong: str, bun: str, ji: str) -> list[dict]:
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        raise SystemExit("DATA_GO_KR_KEY 없음 — .env 에 넣어주세요.")
    q = (f"serviceKey={key}&sigunguCd={sigungu}&bjdongCd={bjdong}"
         f"&bun={bun}&ji={ji}&numOfRows=300&pageNo=1&_type=json")
    for attempt in range(3):              # 타임아웃이 잦다 — 필지 하나를 그냥 버리지 않는다
        try:
            with urllib.request.urlopen(API + "?" + q, timeout=25) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    body = (d.get("response", {}) or {}).get("body", {}) or {}
    items = (body.get("items") or {})
    it = items.get("item") if isinstance(items, dict) else items
    if it is None:
        return []
    return [it] if isinstance(it, dict) else it


def _path(sigungu: str) -> str:
    return os.path.join(ROOT, "data", f"floors-{sigungu}.json")


def load(sigungu: str) -> dict:
    """{pk: '주거'|'비주거'|'미상'} + 이미 조회한 필지 집합."""
    p = _path(sigungu)
    if not os.path.exists(p):
        return {"출처": SRC_DOC, "동": {}, "조회필지": []}
    return json.load(open(p, encoding="utf-8"))


def verdict_of(rows: list[dict]) -> str:
    """한 동의 지하층들 → '주거' | '비주거' | '미상'.

    지하층이 하나도 없으면 '비주거'(반지하 아님). 하나라도 주거면 '주거'.
    나머지가 전부 판정 불가면 '미상' — 반올림하지 않는다.
    """
    지하 = [r for r in rows if (r.get("flrGbCdNm") or "") == "지하"]
    if not 지하:
        return "비주거"
    vs = [classify(r.get("etcPurps")) for r in 지하]
    if True in vs:
        return "주거"
    return "비주거" if any(v is False for v in vs) else "미상"


def collect(sigungu: str, parcels: list[tuple[str, str, str]],
            limit: int = 0, quiet: bool = False) -> dict:
    """parcels: [(법정동코드, 본번4, 부번4)] — 캐시에 없는 것만 조회해 누적 저장."""
    cache = load(sigungu)
    done = set(map(tuple, cache["조회필지"]))
    todo = [p for p in parcels if tuple(p) not in done]
    if limit:
        todo = todo[:limit]
    if not quiet:
        print(f"조회 대상 {len(todo):,}필지 (캐시 {len(done):,}필지 보유)")
    by_pk: dict[str, list[dict]] = {}

    def _flush():
        for pk_, rows_ in by_pk.items():
            cache["동"][pk_] = verdict_of(rows_)
        by_pk.clear()
        os.makedirs(os.path.dirname(_path(sigungu)), exist_ok=True)
        with open(_path(sigungu), "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))

    for i, (bjd, bun, ji) in enumerate(todo, 1):
        try:
            rows = _fetch(sigungu, bjd, bun, ji)
        except Exception as e:                       # 한 필지 실패로 전체를 버리지 않는다
            if not quiet:
                print(f"  ! {bjd}-{bun}-{ji}: {type(e).__name__} {e}")
            continue
        for r in rows:
            by_pk.setdefault(str(r.get("mgmBldrgstPk")), []).append(r)
        cache["조회필지"].append([bjd, bun, ji])
        if i % 50 == 0:
            _flush()                      # 중간 저장 — 끊겨도 여기까지는 남는다
            if not quiet:
                print(f"  {i:,}/{len(todo):,}  (동 {len(cache['동']):,})", flush=True)
        time.sleep(DELAY)
    _flush()
    return cache


def tally(cache: dict, pks) -> tuple[int, int, int]:
    """(반지하 주거 동수, 판정된 동수, 미상 동수). 캐시에 없는 pk 는 미상으로 센다."""
    d = cache["동"]
    주거 = 판정 = 미상 = 0
    for pk in pks:
        v = d.get(str(pk))
        if v == "주거":
            주거 += 1
            판정 += 1
        elif v == "비주거":
            판정 += 1
        else:
            미상 += 1
    return 주거, 판정, 미상


def main(argv=None):
    p = argparse.ArgumentParser(description="건축물대장 층별개요 → 반지하 주거 판정")
    p.add_argument("--zone", help="정비구역 이름 (예: 신림7)")
    p.add_argument("--sigungu", default="11620")
    p.add_argument("--limit", type=int, default=0, help="이번에 조회할 필지 수 상한")
    p.add_argument("--stat", action="store_true", help="캐시 통계")
    a = p.parse_args(argv)

    if a.stat:
        c = load(a.sigungu)
        n = len(c["동"])
        from collections import Counter
        cnt = Counter(c["동"].values())
        print(f"캐시 {_path(a.sigungu)}")
        print(f"  조회 필지 {len(c['조회필지']):,} · 동 {n:,}")
        for k in ("주거", "비주거", "미상"):
            print(f"    {k:<5} {cnt.get(k, 0):>7,}  ({cnt.get(k, 0)/n:.1%})" if n else "")
        return

    if not a.zone:
        raise SystemExit("--zone 또는 --stat 이 필요합니다.")

    import aging, geo, parcel
    zs = geo.search(a.zone)
    if not zs:
        raise SystemExit(f"'{a.zone}' 구역 없음.")
    z = zs[0]
    ag = aging.aggregate_zone(aging.load(), z, parcel.load(a.sigungu))
    pnus = sorted({pnu for _pk, pnu in ag.동목록})
    print(f"구역 {z.name} — 건물 {ag.total:,}동 / 필지 {len(pnus):,}")
    cache = collect(a.sigungu, [(u[5:10], u[11:15], u[15:19]) for u in pnus],
                    limit=a.limit)
    주거, 판정, 미상 = tally(cache, [pk for pk, _pnu in ag.동목록])
    tot = 판정 + 미상
    if tot:
        lo, hi = 주거 / tot, (주거 + 미상) / tot
        print(f"\n반지하 주거 {주거:,}동 / 전체 {tot:,}동 = {lo:.1%} ~ {hi:.1%}"
              f"  (미상 {미상:,})   기준 50% 이상")


if __name__ == "__main__":
    main()
