"""Skip Cloudflare challenges: a real browser solves once, curl_cffi reuses it."""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests

CACHE_FILE = Path.home() / ".cache" / "cloudflare_skip.json"
TIMEOUT_SECONDS = 30
TURNSTILE_HOST = "challenges.cloudflare.com"

__all__ = ["ChallengeError", "get", "wait_for_clearance"]


class ChallengeError(RuntimeError):
    """Cloudflare still challenges the request after a solved clearance."""


def is_challenge(response) -> bool:
    return response.headers.get("cf-mitigated") == "challenge"


def load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_clearance(host: str, clearance: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({**load_cache(), host: clearance}))
    temporary.chmod(0o600)
    temporary.replace(CACHE_FILE)


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


def contains_checkbox(node) -> bool:
    if node.node_name == "INPUT" and "checkbox" in (node.attributes or []):
        return True
    children = [*(node.children or []), *(node.shadow_roots or [])]
    return any(contains_checkbox(child) for child in children)


async def checkbox_ready(browser) -> bool:
    """Whether the Turnstile frame has rendered its checkbox (closed shadow DOM too)."""
    from nodriver import cdp
    from nodriver.core.connection import ProtocolException

    await browser.update_targets()
    for tab in browser.targets:
        if tab.target.type_ != "iframe" or TURNSTILE_HOST not in tab.target.url:
            continue
        try:
            document = await tab.send(cdp.dom.get_document(depth=-1, pierce=True))
        except ProtocolException:
            return False
        return contains_checkbox(document)
    return False


async def tick_checkbox(page) -> None:
    """Focus the Turnstile checkbox with Tab, toggle with Space.

    CDP mouse clicks are rejected by Turnstile; keyboard events are accepted.
    """
    await press(page, "Tab", "Tab", 9)
    await asyncio.sleep(0.4)
    await press(page, " ", "Space", 32, " ")


async def wait_for_clearance(browser, page) -> dict:
    """Tick the checkbox if shown, return cookies + user agent once challenge is gone."""
    ticked = False
    for _ in range(TIMEOUT_SECONDS * 10):
        cookies = {c.name: c.value for c in await browser.cookies.get_all()}
        if "cf_clearance" in cookies and not await page.evaluate(CHALLENGE_SCRIPT):
            user_agent = await page.evaluate("navigator.userAgent")
            return {"cookies": cookies, "user_agent": user_agent}
        if not ticked and await checkbox_ready(browser):
            ticked = True
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


def fetch(url: str, clearance: dict | None = None, **kwargs) -> requests.Response:
    if clearance:
        headers = {**kwargs.get("headers", {}), "User-Agent": clearance["user_agent"]}
        cookies = {**kwargs.get("cookies", {}), **clearance["cookies"]}
        kwargs = {**kwargs, "headers": headers, "cookies": cookies}
    return session.get(url, **kwargs)


def get(url: str, **kwargs) -> requests.Response:
    """GET url, solving the Cloudflare challenge in a browser only when needed.

    Keyword arguments (params, headers, cookies, timeout, ...) go to curl_cffi. The
    user agent is always the solving browser's, since the clearance is bound to it.
    """
    host = urlparse(url).netloc
    response = fetch(url, load_cache().get(host), **kwargs)
    if not is_challenge(response):
        return response

    clearance = asyncio.run(solve_in_browser(url))
    save_clearance(host, clearance)
    response = fetch(url, clearance, **kwargs)
    if is_challenge(response):
        raise ChallengeError(f"{host} still challenges after a solved clearance")
    return response
