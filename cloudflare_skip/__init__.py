"""Skip Cloudflare challenges: a real browser solves once, curl_cffi reuses it."""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests

CACHE_FILE = Path.home() / ".cache" / "cloudflare_skip.json"
TIMEOUT_SECONDS = 30
TICK_AFTER_STEPS = 28

__all__ = ["get", "wait_for_clearance"]


def is_challenge(response) -> bool:
    return response.headers.get("cf-mitigated") == "challenge"


def load_cache() -> dict:
    return json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}


def save_clearance(host: str, clearance: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({**load_cache(), host: clearance}))


CHALLENGE_SCRIPT = "!!window._cf_chl_opt"


async def press(page, key: str, code: str, key_code: int, text: str | None = None):
    from nodriver import cdp

    for kind in ("keyDown", "keyUp"):
        event = cdp.input_.dispatch_key_event(
            kind,
            key=key,
            code=code,
            windows_virtual_key_code=key_code,
            text=text if kind == "keyDown" else None,
        )
        await page.send(event)
        await asyncio.sleep(0.08)


async def tick_checkbox(page) -> None:
    """Focus the Turnstile checkbox with Tab, toggle with Space.

    CDP mouse clicks are rejected by Turnstile; keyboard events are accepted.
    """
    await press(page, "Tab", "Tab", 9)
    await asyncio.sleep(0.4)
    await press(page, " ", "Space", 32, " ")


async def wait_for_clearance(browser, page) -> dict:
    """Tick the checkbox if shown, return cookies + user agent once challenge is gone."""
    for step in range(TIMEOUT_SECONDS * 10):
        cookies = {c.name: c.value for c in await browser.cookies.get_all()}
        if "cf_clearance" in cookies and not await page.evaluate(CHALLENGE_SCRIPT):
            user_agent = await page.evaluate("navigator.userAgent")
            return {"cookies": cookies, "user_agent": user_agent}
        if step == TICK_AFTER_STEPS:
            await tick_checkbox(page)
        await asyncio.sleep(0.1)
    raise TimeoutError(f"challenge not solved in {TIMEOUT_SECONDS}s")


async def solve_in_browser(url: str) -> dict:
    """Open url in a real browser and wait until Cloudflare lets it through."""
    import nodriver

    browser = await nodriver.start()
    try:
        page = await browser.get(url)
        return await wait_for_clearance(browser, page)
    finally:
        browser.stop()


session = requests.Session(impersonate="chrome")


def fetch(url: str, clearance: dict | None = None):
    headers = {"User-Agent": clearance["user_agent"]} if clearance else {}
    cookies = clearance["cookies"] if clearance else {}
    return session.get(url, headers=headers, cookies=cookies)


def get(url: str):
    """GET url, solving the Cloudflare challenge in a browser only when needed."""
    host = urlparse(url).netloc
    response = fetch(url, load_cache().get(host))
    if not is_challenge(response):
        return response

    clearance = asyncio.run(solve_in_browser(url))
    save_clearance(host, clearance)
    return fetch(url, clearance)
