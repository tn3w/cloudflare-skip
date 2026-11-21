"""Record headless Chrome solving a challenge as GIFs: record.py <name> [scheme...]."""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from websockets.sync.client import connect

import cloudflare_skip as skip

URLS = {
    "non-interactive": "https://cl-skip.tn3w.dev/",
    "interactive": "https://cl-skip.tn3w.dev/interactive",
}
WIDTH, HEIGHT = 440, 330
ZOOM = 0.5
HEADLESS_BAR_HEIGHT = 86
FRAMES_PER_SECOND = 20
END_HOLD_SECONDS = 1.5
RESTART_SECONDS = 0.5
ASSETS = Path(__file__).parent.parent / "assets"


def command(connection, page: str, method: str, **params) -> None:
    message = {"id": 0, "sessionId": page, "method": method, "params": params}
    connection.send(json.dumps(message))


def next_frame(connection, page: str) -> bytes | None:
    try:
        message = json.loads(connection.recv(timeout=1 / FRAMES_PER_SECOND))
    except TimeoutError:
        return None
    if message.get("method") != "Page.screencastFrame":
        return None
    frame_session = message["params"]["sessionId"]
    command(connection, page, "Page.screencastFrameAck", sessionId=frame_session)
    return base64.b64decode(message["params"]["data"])


def capture(endpoint: str, target: dict, folder: str, stop: threading.Event) -> None:
    """Screencast the page at a steady frame rate, restarting it after navigations."""
    with connect(endpoint, max_size=None) as connection:
        page = skip.DevTools(connection).attach(target)
        start = time.monotonic()
        last_frame = start - RESTART_SECONDS
        frame = 0
        image = None
        while not stop.is_set():
            due = int((time.monotonic() - start) * FRAMES_PER_SECOND)
            for number in range(frame, due + 1) if image else ():
                Path(folder, f"{number:05d}.png").write_bytes(image)
            frame = due + 1 if image else frame

            if new_image := next_frame(connection, page):
                image, last_frame = new_image, time.monotonic()
            elif time.monotonic() - last_frame >= RESTART_SECONDS:
                command(connection, page, "Page.startScreencast", format="png")
                last_frame = time.monotonic()


def wait_until_passed(browser: skip.DevTools) -> None:
    challenge_title = browser.targets("page")[0]["title"]
    deadline = time.monotonic() + skip.TIMEOUT_SECONDS
    while browser.targets("page")[0]["title"] == challenge_title:
        if time.monotonic() > deadline:
            raise TimeoutError("the challenge page never went away")
        time.sleep(0.05)


def record(url: str, scheme: str, output: Path) -> None:
    executable = skip.find_browser()
    flags = (
        f"--window-size={int(WIDTH / ZOOM)},{int(HEIGHT / ZOOM) + HEADLESS_BAR_HEIGHT}",
        f"--force-device-scale-factor={ZOOM}",
        "--hide-scrollbars",
        f"--blink-settings=preferredColorScheme={int(scheme == 'light')}",
    )
    user_agent = skip.headful_user_agent(executable)
    with (
        tempfile.TemporaryDirectory() as folder,
        skip.open_browser(user_agent, executable, flags=flags) as browser,
    ):
        target = browser.targets("page")[0]
        page = browser.attach(target)
        port = browser.connection.remote_address[1]
        endpoint = f"ws://127.0.0.1:{port}{browser.connection.request.path}"
        stop = threading.Event()
        capturer = threading.Thread(target=capture, args=(endpoint, target, folder, stop))
        browser.send("Page.navigate", page, url=url)
        capturer.start()

        try:
            skip.wait_for_cookies(browser, page, url)
            wait_until_passed(browser)
            time.sleep(END_HOLD_SECONDS)
        finally:
            stop.set()
            capturer.join()

        frames = sorted(map(str, Path(folder).glob("*.png")))
        options = ["--quiet", "--fps", str(FRAMES_PER_SECOND), "-o", str(output)]
        subprocess.run(["gifski", *options, *frames], check=True)


def main(name: str, schemes: list[str]) -> None:
    os.environ["LANGUAGE"] = "en"
    ASSETS.mkdir(exist_ok=True)
    for scheme in schemes:
        output = ASSETS / f"{name}-{scheme}.gif"
        record(URLS[name], scheme, output)
        print(f"{output}: {output.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:] or ["light", "dark"])
