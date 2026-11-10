"""The HTTP layer: pace per host, robots.txt, retries, fallback. HTTP is faked; no network."""
import threading
import time
import unittest
import urllib.error
import urllib.robotparser
from unittest import mock

from jobhunt import net


class Fake:
    """Stands in for net._raw: answers from a script of (status, body, headers) per URL."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append((method, url, body, headers))
        queue = self.script[url]
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(r, Exception):
            raise r
        return r


class FetchJson(unittest.TestCase):
    def setUp(self):
        net._robots.clear()
        net._hosts.clear()
        self.addCleanup(setattr, net, "MIN_GAP", net.MIN_GAP)
        net.MIN_GAP = 0

    def run_with(self, script, *args, **kw):
        fake = Fake(script)
        with mock.patch.object(net, "_raw", side_effect=fake), mock.patch.object(net.time, "sleep"):
            return net.fetch_json(*args, **kw), fake

    def test_get_and_post_json_with_honest_headers(self):
        out, fake = self.run_with({"https://a.example/robots.txt": [(404, b"", {})],
                                   "https://a.example/jobs": [(200, b'{"jobs": [1]}', {})]},
                                  "https://a.example/jobs", data={"q": "ml"})
        self.assertEqual(out, {"jobs": [1]})
        method, _, body, headers = fake.calls[-1]
        self.assertEqual((method, body), ("POST", b'{"q": "ml"}'))
        self.assertEqual(headers["User-Agent"], net.USER_AGENT)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_retries_429_then_succeeds_and_final_errors_raise(self):
        script = {"https://b.example/robots.txt": [(404, b"", {})],
                  "https://b.example/x": [(429, b"", {"retry-after": "2"}), (200, b"[]", {})]}
        out, fake = self.run_with(script, "https://b.example/x")
        self.assertEqual(out, [])
        self.assertEqual(sum(1 for c in fake.calls if c[1].endswith("/x")), 2)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.run_with({"https://b.example/robots.txt": [(404, b"", {})], "https://b.example/gone": [(404, b"", {})]},
                          "https://b.example/gone")
        self.assertEqual(e.exception.code, 404)

    def test_network_errors_retry_then_raise_urlerror(self):
        with self.assertRaises(urllib.error.URLError):
            self.run_with({"https://c.example/robots.txt": [(404, b"", {})], "https://c.example/x": [ConnectionError("reset")]},
                          "https://c.example/x", retries=2)

    def test_robots_disallow_blocks_unless_keyed(self):
        robots = (200, b"User-agent: LinkedInBot\nAllow: /v1/\nUser-agent: *\nDisallow: /\n", {})
        script = {"https://api.d.example/robots.txt": [robots], "https://api.d.example/v1/jobs": [(200, b"{}", {})]}
        with self.assertRaises(net.RobotsBlocked):
            self.run_with(script, "https://api.d.example/v1/jobs")
        out, _ = self.run_with(script, "https://api.d.example/v1/jobs", robots=False)
        self.assertEqual(out, {})

    def test_robots_read_once_per_site(self):
        script = {"https://e.example/robots.txt": [(200, b"User-agent: *\nDisallow: /private\n", {})],
                  "https://e.example/a": [(200, b"1", {})], "https://e.example/b": [(200, b"2", {})]}
        fake = Fake(script)
        with mock.patch.object(net, "_raw", side_effect=fake):
            net.fetch_json("https://e.example/a")
            net.fetch_json("https://e.example/b")
            self.assertFalse(net.allowed("https://e.example/private/x"))
        self.assertEqual(sum(1 for c in fake.calls if c[1].endswith("robots.txt")), 1)


class Pace(unittest.TestCase):
    def test_same_host_waits_other_hosts_do_not(self):
        net._hosts.clear()
        self.addCleanup(setattr, net, "MIN_GAP", net.MIN_GAP)
        net.MIN_GAP = 0.2
        stamps = {}

        def hit(host, key):
            net.pace(host)
            stamps[key] = time.monotonic()
        t0 = time.monotonic()
        threads = [threading.Thread(target=hit, args=("same.example", i)) for i in range(3)] + \
                  [threading.Thread(target=hit, args=(f"other{i}.example", f"o{i}")) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        same = sorted(stamps[i] - t0 for i in range(3))
        self.assertGreaterEqual(same[2] - same[0], 0.35)                 # three calls, two gaps of 0.2 s
        self.assertLess(max(stamps[f"o{i}"] - t0 for i in range(3)), 0.15)


class Fallback(unittest.TestCase):
    def test_works_without_scrapling(self):
        with mock.patch.object(net, "Fetcher", None), mock.patch.object(net.urllib.request, "urlopen") as op:
            op.return_value.__enter__.return_value = mock.Mock(status=200, read=lambda: b'{"ok": true}', headers={})
            self.assertEqual(net._raw("GET", "https://f.example/x", None, {}, 5)[0], 200)


if __name__ == "__main__":
    unittest.main()
