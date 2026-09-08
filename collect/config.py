"""config/sigungu.yaml 로더 — PyYAML 이 없어도 읽히게 최소 파서를 둔다."""
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "config", "sigungu.yaml")
_CACHE = None


def sigungu() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    out = {}
    for line in open(PATH, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        m = re.match(r'"(\d{5})":\s*\{(.+)\}$', line)
        if not m:
            continue
        code, body = m.group(1), m.group(2)
        d = {}
        for k, v in re.findall(r"(\w+):\s*([^,\]]+(?:\[[^\]]*\])?)", body):
            v = v.strip()
            if v.startswith("["):
                d[k] = [x.strip() for x in v.strip("[]").split("|") if x.strip()]
            else:
                d[k] = v
        am = re.search(r"alt:\s*\[([^\]]*)\]", body)
        d["alt"] = [x.strip() for x in am.group(1).split("|")] if am else []
        out[code] = d
    _CACHE = out
    return out
