"""Small HTTP helper: JSON GET/POST with retries on 429 and transient errors."""
import json
import time
import urllib.error
import urllib.request

USER_AGENT = "JobFinderIndia/1.0 (personal job search tool)"


def fetch_json(url, data=None, headers=None, timeout=30, retries=3):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
        if not isinstance(data, (bytes, bytearray)):
            data = json.dumps(data).encode()
    h.update(headers or {})
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # 429 = slow down; 5xx = their side hiccuped. Anything else (404, 401, 422) is final.
            if attempt == retries or not (e.code == 429 or 500 <= e.code < 600):
                raise
            wait = e.headers.get("Retry-After")
            time.sleep(int(wait) if wait and wait.isdigit() else 3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
