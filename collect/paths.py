"""수집물이 놓이는 자리 — 리포에 넣지 않는다(원천은 재현 가능하고 용량이 크다)."""
import os

ROOT = os.path.expanduser(os.environ.get("CTBR_DATA", "~/data/can-this-be-redeveloped"))

CLEANUP = os.path.join(ROOT, "cleanup")          # 정보몽땅 정답 테이블
NOTICES = os.path.join(ROOT, "notices")          # 구청 고시공고
CACHE = os.path.join(ROOT, "cache")              # 원본 HTML (파싱 재현·테스트용)
REPORT = os.path.join(ROOT, "report")


def gu_dir(gu: str, sub: str = "") -> str:
    p = os.path.join(NOTICES, gu, sub) if sub else os.path.join(NOTICES, gu)
    os.makedirs(p, exist_ok=True)
    return p


def ensure() -> str:
    for p in (CLEANUP, NOTICES, CACHE, REPORT):
        os.makedirs(p, exist_ok=True)
    return ROOT
