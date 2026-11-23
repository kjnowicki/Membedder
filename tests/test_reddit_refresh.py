import base64
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from scripts.refresh_reddit_cookies import (
    atomic_write_text,
    decode_seed_cookies_b64,
    filter_reddit_cookies,
    format_json_cookies,
    format_netscape_cookies,
    parse_netscape_cookies,
    parse_seed_cookies,
    verify_cookies_via_http,
)
from sites.reddit import load_reddit_cookies


class _FakeCurlResponse:
    def __init__(self, status_code, body_dict):
        self.status_code = status_code
        self._body = body_dict

    def json(self):
        return self._body


class TestRedditCookieRefresh(unittest.TestCase):
    def test_load_reddit_cookies_supports_httponly(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = os.path.join(tmp, "www.reddit.com_cookies.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write(".reddit.com\tTRUE\t/\tTRUE\t1999999999\tloid\tabc\n")
                f.write("#HttpOnly_.reddit.com\tTRUE\t/\tTRUE\t1999999999\treddit_session\tsess123\n")
                f.write("#HttpOnly_.reddit.com\tTRUE\t/\tTRUE\t1999999999\ttoken_v2\ttok456\n")

            with patch("sites.reddit._get_candidate_cookie_files", return_value=[txt_path]), \
                 patch("sites.cookies._get_candidate_cookie_files", return_value=[txt_path]):
                cookies, found_any = load_reddit_cookies()

            self.assertTrue(found_any)
            self.assertEqual(cookies.get("loid"), "abc")
            self.assertEqual(cookies.get("reddit_session"), "sess123")
            self.assertEqual(cookies.get("token_v2"), "tok456")

    def test_parse_netscape_and_roundtrip(self):
        netscape = (
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.reddit.com\tTRUE\t/\tTRUE\t1999999999\treddit_session\tmy_sess\n"
            ".reddit.com\tTRUE\t/\tFALSE\t0\tcsrf_token\tmy_csrf\n"
            ".example.com\tTRUE\t/\tTRUE\t1999999999\tother\tignored\n"
        )
        parsed = parse_netscape_cookies(netscape)
        self.assertEqual(len(parsed), 3)
        self.assertTrue(parsed[0]["httpOnly"])
        self.assertEqual(parsed[0]["expires"], 1999999999)
        self.assertNotIn("expires", parsed[1])

        reddit_only = filter_reddit_cookies(parsed)
        self.assertEqual(len(reddit_only), 2)

        exported = format_netscape_cookies(reddit_only)
        self.assertIn(".reddit.com\tTRUE\t/\tTRUE\t1999999999\treddit_session\tmy_sess", exported)
        self.assertIn(".reddit.com\tTRUE\t/\tFALSE\t0\tcsrf_token\tmy_csrf", exported)
        self.assertNotIn("example.com", exported)

    def test_decode_seed_cookies_b64_json(self):
        payload = json.dumps([
            {
                "domain": ".reddit.com",
                "name": "reddit_session",
                "value": "sess_val",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expirationDate": 1999999999.5,
                "sameSite": "lax",
            },
            {
                "domain": "google.com",
                "name": "NID",
                "value": "x",
            },
        ])
        b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        decoded = filter_reddit_cookies(decode_seed_cookies_b64(b64))
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0]["name"], "reddit_session")
        self.assertEqual(decoded[0]["value"], "sess_val")
        self.assertEqual(decoded[0]["sameSite"], "Lax")
        self.assertTrue(decoded[0]["httpOnly"])

    def test_format_json_cookies_and_atomic_write(self):
        cookies = [
            {
                "domain": ".reddit.com",
                "name": "token_v2",
                "value": "jwt_token",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 1999999999,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "www.reddit.com_cookies.json")
            atomic_write_text(out_path, format_json_cookies(cookies))
            with open(out_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["name"], "token_v2")
            self.assertEqual(loaded[0]["expirationDate"], 1999999999)

            # Verify parse_seed_cookies reads our own JSON format back cleanly
            reparsed = parse_seed_cookies(json.dumps(loaded))
            self.assertEqual(reparsed[0]["value"], "jwt_token")

    def test_verify_cookies_via_http_success(self):
        cookies = [{"name": "reddit_session", "value": "sess"}]
        responses = [
            _FakeCurlResponse(200, {"data": {"name": "dummy_bot"}}),
            _FakeCurlResponse(200, {"data": {"children": []}}),
        ]
        with patch("curl_cffi.requests.get", side_effect=responses):
            ok, username, detail = verify_cookies_via_http(cookies)
        self.assertTrue(ok)
        self.assertEqual(username, "dummy_bot")
        self.assertEqual(detail, "OK")

    def test_verify_cookies_via_http_logged_out(self):
        cookies = [{"name": "reddit_session", "value": "expired"}]
        with patch("curl_cffi.requests.get", return_value=_FakeCurlResponse(200, {})):
            ok, username, detail = verify_cookies_via_http(cookies)
        self.assertFalse(ok)
        self.assertIsNone(username)
        self.assertIn("NOT logged in", detail)


if __name__ == "__main__":
    unittest.main()
