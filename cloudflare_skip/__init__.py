"""Skip Cloudflare challenges: a headless browser solves once, curl_cffi reuses it."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests
from websockets.sync.client import connect

CACHE_FILE = Path.home() / ".cache" / "cloudflare_skip.json"
TIMEOUT_SECONDS = 30
POLL_SECONDS = 0.02
KEY_SECONDS = 0.02
RETICK_SECONDS = 2
TURNSTILE_HOST = "challenges.cloudflare.com"
BROWSERS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
PLATFORMS = {
    "win32": "Windows NT 10.0; Win64; x64",
    "darwin": "Macintosh; Intel Mac OS X 10_15_7",
}

__all__ = ["ChallengeError", "get", "solve"]


class ChallengeError(RuntimeError):
    """Cloudflare still challenges the request after a solved clearance."""


class DevTools:
    def __init__(self, connection):
        self.connection = connection
        self.counter = 0

    def send(self, method: str, session: str | None = None, **params) -> dict:
        self.counter += 1
        message = {"id": self.counter, "method": method, "params": params}
        if session:
            message["sessionId"] = session
        self.connection.send(json.dumps(message))
        while True:
            reply = json.loads(self.connection.recv())
            if reply.get("id") == self.counter:
                return reply.get("result", {})

    def attach(self, target: dict) -> str:
        options = {"targetId": target["targetId"], "flatten": True}
        return self.send("Target.attachToTarget", **options)["sessionId"]

    def targets(self, kind: str) -> list[dict]:
        targets = self.send("Target.getTargets")["targetInfos"]
        return [target for target in targets if target["type"] == kind]

    def press_space(self, session: str):
        for kind in ("keyDown", "keyUp"):
            self.send(
                "Input.dispatchKeyEvent",
                session,
                type=kind,
                key=" ",
                code="Space",
                windowsVirtualKeyCode=32,
                text=" " if kind == "keyDown" else "",
            )
            time.sleep(KEY_SECONDS)


def find_browser() -> str:
    for name in (os.environ.get("CHROME_PATH"), *BROWSERS):
        if name and (path := shutil.which(name)):
            return path
    raise FileNotFoundError("Chrome or Chromium not found, set CHROME_PATH")


def headful_user_agent(executable: str) -> str:
    """The user agent headful Chrome would send, so headless does not say so."""
    output = subprocess.run([executable, "--version"], capture_output=True, text=True)
    major = re.search(r"(\d+)\.\d+\.\d+", output.stdout)[1]
    platform = PLATFORMS.get(sys.platform, "X11; Linux x86_64")
    return (
        f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def free_port() -> int:
    """A fixed port: Cloudflare rejects clearances from --remote-debugging-port=0."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def browser_arguments(
    executable: str, profile: str, user_agent: str, headless: bool
) -> list[str]:
    arguments = [
        executable,
        f"--remote-debugging-port={free_port()}",
        f"--user-data-dir={profile}",
        f"--user-agent={user_agent}",
        "--no-first-run",
        "--disable-dev-shm-usage",
    ]
    if os.name == "posix" and os.geteuid() == 0:
        arguments.append("--no-sandbox")
    if headless:
        arguments.append("--headless=new")
    return arguments


def wait_for_endpoint(process: subprocess.Popen, log: Path) -> str:
    while process.poll() is None:
        if endpoint := re.search(r"ws://\S+", log.read_text()):
            return endpoint[0]
        time.sleep(0.01)
    raise RuntimeError(f"browser exited with code {process.returncode}")


@contextmanager
def open_browser(
    user_agent: str, executable: str, headless: bool = True, flags: tuple[str, ...] = ()
):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        log = Path(profile, "chrome.log")
        arguments = browser_arguments(executable, profile, user_agent, headless)
        with log.open("w") as output:
            process = subprocess.Popen(
                [*arguments, *flags, "about:blank"], stdout=output, stderr=output
            )
        try:
            with connect(wait_for_endpoint(process, log), max_size=None) as connection:
                yield DevTools(connection)
        finally:
            process.kill()
            process.wait()


def find_checkbox(node: dict) -> int | None:
    if node.get("nodeName") == "INPUT" and "checkbox" in node.get("attributes", []):
        return node["nodeId"]
    for child in [*node.get("children", []), *node.get("shadowRoots", [])]:
        if checkbox := find_checkbox(child):
            return checkbox
    return None


def find_turnstile(browser: DevTools) -> str | None:
    for frame in browser.targets("iframe"):
        if TURNSTILE_HOST in frame["url"]:
            return browser.attach(frame)
    return None


def tick_checkbox(browser: DevTools, page: str, turnstile: str) -> bool:
    """Focus the checkbox, press Space: Turnstile rejects CDP mouse clicks."""
    document = browser.send("DOM.getDocument", turnstile, depth=-1, pierce=True)
    checkbox = find_checkbox(document.get("root", {}))
    if not checkbox:
        return False
    browser.send("DOM.focus", turnstile, nodeId=checkbox)
    browser.press_space(page)
    return True


def read_cookies(browser: DevTools, page: str, url: str) -> dict:
    cookies = browser.send("Network.getCookies", page, urls=[url])["cookies"]
    return {cookie["name"]: cookie["value"] for cookie in cookies}


def wait_for_cookies(browser: DevTools, page: str, url: str) -> dict:
    turnstile = None
    ticked_at = -RETICK_SECONDS
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        cookies = read_cookies(browser, page, url)
        if "cf_clearance" in cookies:
            return cookies
        turnstile = turnstile or find_turnstile(browser)
        due = turnstile and time.monotonic() - ticked_at > RETICK_SECONDS
        if due and tick_checkbox(browser, page, turnstile):
            ticked_at = time.monotonic()
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"challenge not solved in {TIMEOUT_SECONDS}s")


def solve(url: str, headless: bool = True) -> dict:
    """Open url in Chrome, tick Turnstile if shown, return cookies and user agent."""
    executable = find_browser()
    user_agent = headful_user_agent(executable)
    with open_browser(user_agent, executable, headless) as browser:
        page = browser.attach(browser.targets("page")[0])
        browser.send("Page.navigate", page, url=url)
        cookies = wait_for_cookies(browser, page, url)
    return {"cookies": cookies, "user_agent": user_agent}


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

    clearance = solve(url)
    save_clearance(host, clearance)
    response = fetch(url, clearance, **kwargs)
    if is_challenge(response):
        raise ChallengeError(f"{host} still challenges after a solved clearance")
    return response
