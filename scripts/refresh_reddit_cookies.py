#!/usr/bin/env python3
"""
Refreshes and prolongs Reddit session cookies using a persistent Playwright
Chromium profile, then exports them to Netscape (.txt) format for Membedder.

Typical flow:
1. Opens a persistent browser profile at --profile-dir so Reddit naturally
   rotates token_v2 and extends reddit_session on each visit.
2. Optionally seeds cookies from a file or REDDIT_SEED_COOKIES_B64 env var
   (useful for bootstrapping on a headless server if login hits a CAPTCHA).
3. If the profile is not logged in, falls back to logging in with
   REDDIT_USERNAME and REDDIT_PASSWORD env vars.
4. Verifies authentication via /api/me.json and a real .json post listing.
5. Atomically writes www.reddit.com_cookies.txt (and updates .json if present).
"""

import argparse
import base64
import datetime
import json
import os
import sys
import tempfile
import time

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

REDDIT_AUTH_COOKIES = ("reddit_session", "token_v2")


def parse_netscape_cookies(content):
    """Parses Netscape cookies.txt text into a list of Playwright-compatible cookie dicts."""
    cookies = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) < 7:
            continue

        domain, _flag, path, secure_str, expires_str, name, value = parts[:7]
        if not domain or not name:
            continue

        cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure_str.upper() == "TRUE",
            "httpOnly": http_only,
        }
        try:
            expires = int(float(expires_str))
            if expires > 0:
                cookie["expires"] = expires
        except ValueError:
            pass
        cookies.append(cookie)
    return cookies


def parse_seed_cookies(raw_text):
    """
    Parses seed cookies from either a JSON array (browser extension export)
    or Netscape cookies.txt text into Playwright-compatible cookie dicts.
    """
    text = raw_text.strip()
    if not text:
        return []

    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                cookies = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    value = item.get("value")
                    domain = item.get("domain")
                    if not name or value is None or not domain:
                        continue
                    cookie = {
                        "name": str(name),
                        "value": str(value),
                        "domain": str(domain),
                        "path": str(item.get("path") or "/"),
                        "secure": bool(item.get("secure", False)),
                        "httpOnly": bool(item.get("httpOnly", False)),
                    }
                    exp = item.get("expirationDate")
                    if exp is None:
                        exp = item.get("expires")
                    if exp is not None:
                        try:
                            exp_val = float(exp)
                            if exp_val > 0:
                                cookie["expires"] = exp_val
                        except (ValueError, TypeError):
                            pass
                    same_site = item.get("sameSite")
                    if isinstance(same_site, str):
                        normalized = same_site.strip().lower()
                        if normalized in ("strict", "lax", "none"):
                            cookie["sameSite"] = normalized.capitalize()
                    cookies.append(cookie)
                return cookies
        except json.JSONDecodeError:
            pass

    return parse_netscape_cookies(text)


def decode_seed_cookies_b64(b64_value):
    """Decodes a base64-encoded cookie file (Netscape or JSON) into Playwright cookie dicts."""
    cleaned = "".join(b64_value.split())
    if not cleaned:
        return []
    raw_bytes = base64.b64decode(cleaned)
    return parse_seed_cookies(raw_bytes.decode("utf-8"))


def filter_reddit_cookies(cookies):
    """Filters a list of cookie dicts to Reddit domains only."""
    result = []
    for c in cookies:
        domain = (c.get("domain") or "").lower().lstrip(".")
        if domain == "reddit.com" or domain.endswith(".reddit.com"):
            result.append(c)
    return result


def format_netscape_cookies(cookies):
    """Formats Playwright cookie dicts as a Netscape cookies.txt string."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# http://curl.haxx.se/rfc/cookie_spec.html",
        "# Generated by scripts/refresh_reddit_cookies.py",
        "",
    ]
    for c in cookies:
        domain = c.get("domain", "")
        name = c.get("name", "")
        value = c.get("value", "")
        if not domain or not name:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        expires_raw = c.get("expires", 0)
        try:
            expires = int(float(expires_raw))
            if expires < 0:
                expires = 0
        except (ValueError, TypeError):
            expires = 0
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def format_json_cookies(cookies):
    """Formats Playwright cookie dicts into browser-extension-style JSON."""
    exported = []
    for c in cookies:
        entry = {
            "domain": c.get("domain", ""),
            "httpOnly": bool(c.get("httpOnly", False)),
            "name": c.get("name", ""),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", False)),
            "value": c.get("value", ""),
        }
        exp = c.get("expires")
        if exp is not None and exp > 0:
            entry["expirationDate"] = exp
        exported.append(entry)
    return json.dumps(exported, indent=2) + "\n"


def atomic_write_text(path, content):
    """Writes text atomically via a temporary file in the same directory."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".cookies_tmp_", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def log_cookie_summary(cookies):
    """Logs auth cookie metadata (names & expiry timestamps only — never values)."""
    now = time.time()
    by_name = {c.get("name"): c for c in cookies}
    print(f"[REFRESH] Total Reddit cookies exported: {len(cookies)}")
    for name in REDDIT_AUTH_COOKIES:
        c = by_name.get(name)
        if not c:
            print(f"[REFRESH]   - {name}: MISSING")
            continue
        exp = c.get("expires", 0) or 0
        if exp > 0:
            dt = datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
            days_left = (exp - now) / 86400.0
            print(f"[REFRESH]   - {name}: expires {dt.strftime('%Y-%m-%d %H:%M UTC')} ({days_left:.1f} days left)")
        else:
            print(f"[REFRESH]   - {name}: session cookie (no fixed expiry)")


def verify_cookies_via_http(cookies):
    """
    Verifies exported cookies using curl_cffi with Chrome TLS impersonation
    (the exact HTTP stack Membedder uses in sites/reddit.py) against Reddit's
    /api/me.json and a public post listing .json endpoint.
    Returns (ok, username, detail_message).
    """
    from curl_cffi import requests as curl_requests

    cookie_dict = {c["name"]: c["value"] for c in cookies if c.get("name")}

    # 1. Verify logged-in identity
    username = None
    try:
        resp = curl_requests.get(
            "https://www.reddit.com/api/me.json",
            cookies=cookie_dict,
            impersonate="chrome",
            timeout=20,
        )
        if resp.status_code != 200:
            return False, None, f"/api/me.json returned HTTP {resp.status_code}"
        me_data = resp.json()
        username = (me_data.get("data") or {}).get("name")
        if not username:
            return False, None, "/api/me.json indicates session is NOT logged in"
    except Exception as e:
        return False, None, f"/api/me.json request error: {e}"

    # 2. Verify a real .json post fetch succeeds
    try:
        resp = curl_requests.get(
            "https://www.reddit.com/r/announcements/top/.json?limit=1",
            cookies=cookie_dict,
            impersonate="chrome",
            timeout=20,
        )
        if resp.status_code != 200:
            return False, username, f"Post JSON test returned HTTP {resp.status_code}"
        listing = resp.json()
        if "data" not in listing:
            return False, username, "Post JSON test returned unexpected structure"
    except Exception as e:
        return False, username, f"Post JSON test error: {e}"

    return True, username, "OK"


def _check_logged_in_context(context):
    """Checks via Playwright's APIRequestContext if the current browser profile is authenticated."""
    try:
        resp = context.request.get("https://www.reddit.com/api/me.json", timeout=20000)
        if resp.status == 200:
            data = resp.json()
            name = (data.get("data") or {}).get("name")
            if name:
                return True, name
    except Exception as e:
        print(f"[REFRESH] Auth check via /api/me.json failed: {e}")
    return False, None


def _attempt_login(page, context, username, password, debug_screenshot_path=None):
    """Attempts to log into Reddit using the provided username and password."""
    print("[REFRESH] Attempting Reddit web login...")
    page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)

    # Playwright CSS locators automatically pierce open Shadow DOM (used by Reddit's faceplate-text-input)
    user_input = page.locator(
        'input[name="username"], #login-username input, faceplate-text-input[name="username"] input'
    ).first
    pass_input = page.locator(
        'input[name="password"], #login-password input, faceplate-text-input[name="password"] input'
    ).first

    user_input.wait_for(state="visible", timeout=20000)
    user_input.click()
    user_input.fill(username)

    pass_input.wait_for(state="visible", timeout=15000)
    pass_input.click()
    pass_input.fill(password)

    # Submit form (prefer explicit Log In button, fallback to pressing Enter)
    submit_btn = page.locator(
        'button[type="submit"], button.login, shreddit-overlay-display button:has-text("Log In"), button:has-text("Log In")'
    ).first
    if submit_btn.count() > 0:
        submit_btn.click()
    else:
        pass_input.press("Enter")

    # Wait up to 25 seconds for auth to succeed
    deadline = time.time() + 25
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        logged_in, name = _check_logged_in_context(context)
        if logged_in:
            print(f"[REFRESH] Login succeeded as u/{name}")
            return True, name

        # Detect obvious CAPTCHA / challenge walls early
        captcha_frames = page.locator(
            'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="arkose"], iframe[src*="turnstile"]'
        )
        if captcha_frames.count() > 0:
            print("[REFRESH] ERROR: Login blocked by CAPTCHA / anti-bot challenge.")
            break

    if debug_screenshot_path:
        try:
            page.screenshot(path=debug_screenshot_path, full_page=True)
            print(f"[REFRESH] Saved debug screenshot to {debug_screenshot_path}")
        except Exception as e:
            print(f"[REFRESH] Could not save debug screenshot: {e}")

    return False, None


def refresh_cookies(
    profile_dir,
    output_dir,
    seed_file=None,
    seed_b64=None,
    username=None,
    password=None,
    force_login=False,
    force_seed=False,
    headless=True,
):
    from playwright.sync_api import sync_playwright

    os.makedirs(profile_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    debug_screenshot = os.path.join(output_dir, ".reddit_refresh_debug.png")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()

            # 1. Optional cookie seeding (explicit --force-seed or when profile isn't logged in yet)
            seed_cookies = []
            if seed_b64:
                seed_cookies = filter_reddit_cookies(decode_seed_cookies_b64(seed_b64))
                print(f"[REFRESH] Loaded {len(seed_cookies)} seed cookies from REDDIT_SEED_COOKIES_B64")
            elif seed_file and os.path.exists(seed_file):
                with open(seed_file, "r", encoding="utf-8") as f:
                    seed_cookies = filter_reddit_cookies(parse_seed_cookies(f.read()))
                print(f"[REFRESH] Loaded {len(seed_cookies)} seed cookies from {seed_file}")

            if seed_cookies and force_seed:
                context.add_cookies(seed_cookies)
                print("[REFRESH] Force-seeded cookies into persistent browser profile.")

            # 2. Load Reddit homepage to let Reddit refresh/prolong existing session cookies
            logged_in = False
            logged_in_user = None
            if not force_login:
                print("[REFRESH] Visiting https://www.reddit.com/ to refresh session...")
                page.goto("https://www.reddit.com/", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                logged_in, logged_in_user = _check_logged_in_context(context)

                # If not logged in yet and seed cookies are available, inject them and reload
                if not logged_in and seed_cookies and not force_seed:
                    print("[REFRESH] Profile not logged in; seeding cookies and reloading...")
                    context.add_cookies(seed_cookies)
                    page.goto("https://www.reddit.com/", wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(5000)
                    logged_in, logged_in_user = _check_logged_in_context(context)

            # 3. Fallback to dummy-account login if still not logged in (or --force-login)
            if not logged_in:
                if force_login:
                    context.clear_cookies()
                if username and password:
                    logged_in, logged_in_user = _attempt_login(
                        page, context, username, password, debug_screenshot_path=debug_screenshot
                    )
                    if logged_in:
                        # Visit homepage after login so all session/token_v2 cookies settle
                        page.goto("https://www.reddit.com/", wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(5000)
                else:
                    print(
                        "[REFRESH] ERROR: Persistent browser profile is not logged in, "
                        "and REDDIT_USERNAME / REDDIT_PASSWORD secrets are not configured."
                    )

            if not logged_in:
                print(
                    "[REFRESH] FAILED: Could not establish an authenticated Reddit session.\n"
                    "  If login was blocked by a CAPTCHA on your server, export fresh Reddit cookies\n"
                    "  from your PC browser, base64-encode them into the REDDIT_SEED_COOKIES_B64\n"
                    "  GitHub secret, and re-run this workflow with seed_from_secret=true."
                )
                return 1

            print(f"[REFRESH] Authenticated session confirmed for u/{logged_in_user}")

            # 4. Extract Reddit cookies from persistent context
            all_cookies = context.cookies(["https://www.reddit.com", "https://old.reddit.com"])
            reddit_cookies = filter_reddit_cookies(all_cookies)

            if not reddit_cookies:
                print("[REFRESH] ERROR: No Reddit cookies found in browser context.")
                return 1

            log_cookie_summary(reddit_cookies)

            # 5. End-to-end verification using plain HTTP (matches Membedder's aiohttp requests)
            ok, verified_user, detail = verify_cookies_via_http(reddit_cookies)
            if not ok:
                print(f"[REFRESH] ERROR: Exported cookies failed HTTP verification: {detail}")
                return 1
            print(f"[REFRESH] End-to-end HTTP verification passed as u/{verified_user}")

            # 6. Atomically export cookies to output_dir
            txt_path = os.path.join(output_dir, "www.reddit.com_cookies.txt")
            atomic_write_text(txt_path, format_netscape_cookies(reddit_cookies))
            print(f"[REFRESH] Wrote {txt_path}")

            # Keep www.reddit.com_cookies.json in sync if it already exists so no stale copy remains
            json_path = os.path.join(output_dir, "www.reddit.com_cookies.json")
            if os.path.exists(json_path):
                atomic_write_text(json_path, format_json_cookies(reddit_cookies))
                print(f"[REFRESH] Updated existing {json_path}")

            # Clean up any old debug screenshot on success
            if os.path.exists(debug_screenshot):
                os.remove(debug_screenshot)

            return 0
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description="Refresh Reddit cookies via persistent Playwright profile.")
    parser.add_argument(
        "--profile-dir",
        default="browser_profiles/reddit",
        help="Persistent Chromium user data directory.",
    )
    parser.add_argument(
        "--output-dir",
        default="cookies",
        help="Directory where www.reddit.com_cookies.txt will be written.",
    )
    parser.add_argument(
        "--seed-file",
        default=None,
        help="Optional Netscape (.txt) or JSON cookie file to seed the profile if not logged in.",
    )
    parser.add_argument(
        "--force-seed",
        action="store_true",
        help="Inject seed cookies even if the profile already has cookies.",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Clear existing cookies and perform a fresh login using REDDIT_USERNAME/REDDIT_PASSWORD.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser with a visible window (useful for local one-time manual seeding).",
    )
    args = parser.parse_args()

    rc = refresh_cookies(
        profile_dir=args.profile_dir,
        output_dir=args.output_dir,
        seed_file=args.seed_file,
        seed_b64=os.environ.get("REDDIT_SEED_COOKIES_B64", "").strip() or None,
        username=os.environ.get("REDDIT_USERNAME", "").strip() or None,
        password=os.environ.get("REDDIT_PASSWORD", "").strip() or None,
        force_login=args.force_login,
        force_seed=args.force_seed,
        headless=not args.headed,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
