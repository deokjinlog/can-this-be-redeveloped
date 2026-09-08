"""예의 있는 HTTP — 남의 서버를 긁는 코드가 지켜야 할 것들.

  · robots.txt 를 먼저 읽고 막힌 경로는 요청하지 않는다
  · 요청 사이를 띄운다(기본 2초). 호스트별로 마지막 요청 시각을 기억한다
  · 정체를 밝히는 User-Agent 와 연락처
  · 받은 HTML 은 캐시한다 — 같은 걸 두 번 받지 않고, 파서 테스트도 이걸로 한다
"""
import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser as robotparser

from . import paths

UA = ("can-this-be-redeveloped/1.0 (personal, non-commercial research; "
      "contact: deokjinlog@gmail.com)")
DELAY = 2.0                      # 같은 호스트 재요청 최소 간격(초)
TIMEOUT = 30

_last: dict = {}
_robots: dict = {}
_denyall: dict = {}
_raw: dict = {}


class Blocked(Exception):
    """robots.txt 가 막은 경로 — 우회하지 않고 그대로 멈춘다."""


def _fetch_robots(host: str):
    """표준 파서 + 원문. 원문을 따로 보는 이유는 아래 _deny_all 참조."""
    rp = robotparser.RobotFileParser()
    rp.set_url(f"https://{host}/robots.txt")
    raw = ""
    try:
        req = urllib.request.Request(f"https://{host}/robots.txt",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
        rp.parse(raw.splitlines())
    except Exception:
        rp = None
    return rp, raw


def _rules(raw: str) -> list:
    """'*' 에게 주는 Allow/Disallow 규칙만 뽑는다. [(허용여부, 경로), …]"""
    out, applies = [], False
    for line in raw.splitlines():
        t = line.split("#", 1)[0].strip()
        if not t:
            continue
        k, _, v = t.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            applies = v in ("*", UA)
        elif applies and k in ("allow", "disallow") and v:
            out.append((k == "allow", v))
    return out


def _longest_match(raw: str, path: str):
    """RFC 9309 — 가장 구체적인(긴) 규칙이 이긴다. 못 정하면 None.

    파이썬 robotparser 는 '첫 매칭' 을 쓰는 옛 규칙이라, 'Disallow: /' 뒤에
    'Allow: /news' 를 둔 사이트(서울시)를 통째로 막힌 것으로 읽는다.
    사이트가 Allow 를 명시했다면 그건 허용하겠다는 뜻이다.
    """
    best = None
    for allow, pat in _rules(raw):
        p = pat.rstrip("$")
        if not path.startswith(p):
            continue
        if pat.endswith("$") and path != p:
            continue
        # 길이가 같으면 Allow 가 이긴다(RFC 9309)
        if best is None or len(p) > best[1] or (len(p) == best[1] and allow):
            best = (allow, len(p))
    return None if best is None else best[0]


def _deny_all(raw: str) -> bool:
    """'User-agent:' 없이 'Disallow: /' 만 있는 robots.txt 를 전면 차단으로 읽는다.

    표준 파서는 어느 User-agent 에도 귀속되지 않은 규칙을 무시한다. 그래서
    영등포구처럼 파일 전체가 `Disallow: /` 한 줄인 경우 '허용' 으로 잘못 판정된다.
    형식이 깨졌어도 의도는 명백하다 — 우리는 우회하지 않는다.
    """
    seen_ua = False
    for line in raw.splitlines():
        t = line.split("#", 1)[0].strip()
        if not t:
            continue
        k, _, v = t.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            seen_ua = True
        elif k == "disallow" and v == "/" and not seen_ua:
            return True
    return False


def allowed(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc
    if host not in _robots:
        rp, raw = _fetch_robots(host)
        _robots[host] = rp
        _denyall[host] = _deny_all(raw)
        _raw[host] = raw
    if _denyall.get(host):
        return False
    rp = _robots[host]
    if rp is None:
        return True              # robots 를 못 읽으면 허용하되 간격은 지킨다
    if rp.can_fetch(UA, url) and rp.can_fetch("*", url):
        return True
    # 표준 파서가 막았어도, 더 구체적인 Allow 가 있으면 그게 사이트의 뜻이다
    path = urllib.parse.urlsplit(url).path or "/"
    return _longest_match(_raw.get(host, ""), path) is True


def _wait(host: str):
    t = _last.get(host)
    if t is not None:
        gap = DELAY - (time.time() - t)
        if gap > 0:
            time.sleep(gap)
    _last[host] = time.time()


def _cache_path(url: str) -> str:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    host = urllib.parse.urlsplit(url).netloc.replace(":", "_")
    d = os.path.join(paths.CACHE, host)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ".html")


def get(url: str, *, cache: bool = True, binary: bool = False,
        referer: str = None, encoding: str = "utf-8"):
    """GET. cache=True 면 받은 본문을 저장하고 다음엔 그걸 쓴다."""
    if not allowed(url):
        raise Blocked(f"robots.txt 가 막은 경로입니다: {url}")
    cp = _cache_path(url)
    # 캐시는 항상 UTF-8 로 쓴다. 원문이 EUC-KR 이어도 디코딩 후 저장하므로,
    # 캐시 읽기까지 원문 인코딩을 쓰면 깨진다(eminwon 이 EUC-KR 이다).
    if cache and not binary and os.path.exists(cp):
        return open(cp, encoding="utf-8", errors="replace").read()

    host = urllib.parse.urlsplit(url).netloc
    _wait(host)
    h = {"User-Agent": UA, "Accept-Language": "ko"}
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
        disp = r.headers.get("Content-Disposition", "")
    if binary:
        return raw, ctype, disp
    txt = raw.decode(encoding, "replace")
    if cache:
        with open(cp, "w", encoding="utf-8") as fh:
            fh.write(txt)
    return txt


def download(url: str, dest: str, referer: str = None) -> tuple[int, str]:
    """첨부 내려받기. (바이트수, Content-Type)"""
    if not allowed(url):
        raise Blocked(f"robots.txt 가 막은 경로입니다: {url}")
    raw, ctype, _ = get(url, binary=True, referer=referer)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(raw)
    return len(raw), ctype
