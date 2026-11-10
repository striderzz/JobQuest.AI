"""HTTP for job boards.

JSON GET/POST through Scrapling's plain HTTP fetcher (curl based: fast, HTTP/2, connection reuse). No browser, no
fingerprint spoofing and no bot-check tricks: requests say honestly what they are. On top of that:
* a polite pace per host (MIN_GAP seconds between requests to the same site, shared by all search threads);
* robots.txt is respected for anything read without a key (keyed APIs you signed up for pass robots=False);
* 429 and 5xx are retried, honouring Retry-After; other errors (404, 401, 422) are final.
Falls back to the standard library when Scrapling isn't installed.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from email.message import Message
from urllib.parse import urlsplit

USER_AGENT = "JobQuestAI/4.0 (personal job search tool)"
ROBOTS_AGENT = "JobQuestAI"
MIN_GAP = 0.5               # seconds between two requests to the same host
HOST_GAP = {"apply.careers.microsoft.com": 3.0, "careers.qualcomm.com": 2.0}   # sites that ask for a slower pace

_pace_lock = threading.Lock()
_hosts = {}                 # host -> [lock, time of the last request]
_robots_lock = threading.Lock()
_robots = {}                # scheme://host -> RobotFileParser, or True when the site has no usable robots.txt

try:
    from scrapling.fetchers import Fetcher
    logging.getLogger("scrapling").setLevel(logging.WARNING)       # one log line per request is too chatty
except Exception:            # not installed: the standard library does the same job, a little slower
    Fetcher = None


class RobotsBlocked(Exception):
    """The site's robots.txt asks tools not to read this address."""


def _raw(method, url, body, headers, timeout):
    """One request, no retries. Returns (status, body bytes, headers). Raises only on network failure."""
    if Fetcher is not None:
        kw = dict(headers=headers, timeout=timeout, retries=1, impersonate=None, stealthy_headers=False,
                  follow_redirects="safe")
        r = Fetcher.post(url, data=body, **kw) if method == "POST" else Fetcher.get(url, **kw)
        return r.status, r.body or b"", dict(r.headers or {})
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", dict(e.headers or {})


def pace(host):
    """Wait until this host may be asked again. Different hosts never wait for each other."""
    with _pace_lock:
        slot = _hosts.setdefault(host, [threading.Lock(), 0.0])
    with slot[0]:
        wait = slot[1] + HOST_GAP.get(host, MIN_GAP) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        slot[1] = time.monotonic()


def _load_robots(base):
    try:
        status, body, _ = _raw("GET", base + "/robots.txt", None, {"User-Agent": USER_AGENT}, 15)
    except Exception:
        return True                       # can't read it: nothing to respect
    if status >= 400:
        return True                       # no robots.txt (or it's private) means no rules
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.decode("utf-8", "replace").splitlines())
    return rp


def allowed(url):
    """robots.txt check, read once per site per app run."""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    with _robots_lock:
        rp = _robots.get(base)
    if rp is None:
        rp = _load_robots(base)
        with _robots_lock:
            _robots[base] = rp
    return rp is True or rp.can_fetch(ROBOTS_AGENT, url)


def fetch_json(url, data=None, headers=None, timeout=30, retries=3, robots=True):
    """GET (or POST when `data` is given) and parse JSON. Pass robots=False only for an API you use with your own key
    under its terms (Adzuna, JSearch, Jooble)."""
    if robots and not allowed(url):
        raise RobotsBlocked(f"{urlsplit(url).netloc} asks tools not to read {urlsplit(url).path}")
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
        if not isinstance(data, (bytes, bytearray)):
            data = json.dumps(data).encode()
    h.update(headers or {})
    host = urlsplit(url).netloc
    method = "POST" if data is not None else "GET"
    for attempt in range(retries + 1):
        pace(host)
        try:
            status, body, hdrs = _raw(method, url, data, h, timeout)
        except Exception as e:            # network trouble: connection reset, DNS, timeout
            if attempt == retries:
                raise urllib.error.URLError(str(e)[:200]) from e
            time.sleep(2 * (attempt + 1))
            continue
        if 200 <= status < 300:
            return json.loads(body.decode("utf-8", "replace") or "null")
        # 429 = slow down; 5xx = their side hiccuped. Anything else (404, 401, 422) is final.
        if attempt == retries or not (status == 429 or 500 <= status < 600):
            msg = Message()
            for k, v in hdrs.items():
                msg[k] = v
            raise urllib.error.HTTPError(url, status, f"HTTP {status}", msg, None)
        wait = str(hdrs.get("retry-after") or hdrs.get("Retry-After") or "")
        time.sleep(min(60, int(wait)) if wait.isdigit() else 3 * (attempt + 1))
