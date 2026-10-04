"""Record Chrome solving the challenge as light + dark GIFs: record.py <name> [scheme]."""

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import nodriver
from nodriver import cdp

from cloudflare_skip import wait_for_clearance

URL = "https://cl-skip.tn3w.dev/"
RECORDER_URL = "https://example.com/"
TARGET_TITLE = "recording-target"
WIDTH, HEIGHT = 440, 330
PAGE_ZOOM = 0.5
SHARING_BAR_HEIGHT = 56
FRAMES_PER_SECOND = 20
END_HOLD_SECONDS = 1.5
SKIP_BLANK_SECONDS = 0.2
ASSETS = Path(__file__).parent.parent / "assets"

START_SCRIPT = """
(async () => {
    const stream = await navigator.mediaDevices.getDisplayMedia({
        video: {
            cursor: "never",
            frameRate: 30,
            width: %(width)d,
            height: %(height)d,
        },
    });
    window.chunks = [];
    window.recorder = new MediaRecorder(stream, {videoBitsPerSecond: 8000000});
    recorder.ondataavailable = (event) => chunks.push(event.data);
    recorder.start(100);
})()
"""

STOP_SCRIPT = """
new Promise((resolve) => {
    recorder.onstop = () => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(",")[1]);
        reader.readAsDataURL(new Blob(chunks, {type: "video/webm"}));
    };
    recorder.stop();
})
"""


async def run_script(page, script: str) -> str:
    result, error = await page.send(
        cdp.runtime.evaluate(
            script, await_promise=True, user_gesture=True, return_by_value=True
        )
    )
    if error:
        raise RuntimeError(error.exception.description)
    return result.value


async def viewport_size(page) -> tuple[int, int]:
    script = "JSON.stringify([innerWidth, innerHeight])"
    width, height = json.loads(await page.evaluate(script))
    return width, height


async def fit_window(page) -> None:
    """Size the window so the viewport is WIDTH x HEIGHT once the sharing bar shows."""
    window_id, _ = await page.get_window()
    normal = cdp.browser.Bounds(window_state=cdp.browser.WindowState.NORMAL)
    await page.send(cdp.browser.set_window_bounds(window_id, normal))
    wanted = (int(WIDTH / PAGE_ZOOM), int(HEIGHT / PAGE_ZOOM) + SHARING_BAR_HEIGHT)
    for _ in range(10):
        await asyncio.sleep(0.2)
        width, height = await viewport_size(page)
        if (width, height) == wanted:
            return
        _, bounds = await page.get_window()
        fitted = cdp.browser.Bounds(
            left=0,
            top=0,
            width=bounds.width - width + wanted[0],
            height=bounds.height - height + wanted[1],
        )
        await page.send(cdp.browser.set_window_bounds(window_id, fitted))
    raise RuntimeError(f"window did not fit {wanted}, last viewport {(width, height)}")


async def wait_until_secure(page) -> None:
    while not await page.evaluate("isSecureContext"):
        await asyncio.sleep(0.05)


def video_to_gif(video: Path, output: Path) -> None:
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(
            [
                *("ffmpeg", "-y", "-loglevel", "error", "-ss", str(SKIP_BLANK_SECONDS)),
                *("-i", str(video), "-vf", f"fps={FRAMES_PER_SECOND}"),
                str(Path(directory, "%04d.png")),
            ],
            check=True,
        )
        subprocess.run(
            [
                *("gifski", "--quiet", "--fps", str(FRAMES_PER_SECOND)),
                *("-o", str(output)),
                *map(str, sorted(Path(directory).glob("*.png"))),
            ],
            check=True,
        )


async def record(scheme: str, output: Path) -> None:
    color_scheme = f"--blink-settings=preferredColorScheme={int(scheme == 'light')}"
    browser_args = [
        f"--auto-select-tab-capture-source-by-title={TARGET_TITLE}",
        "--hide-scrollbars",
        f"--force-device-scale-factor={PAGE_ZOOM}",
        color_scheme,
    ]
    browser = await nodriver.start(browser_args=browser_args)
    try:
        target = await browser.get("about:blank")
        await target.evaluate(f"document.title = '{TARGET_TITLE}'")
        await fit_window(target)
        recorder = await browser.get(RECORDER_URL, new_tab=True)
        await wait_until_secure(recorder)
        await run_script(recorder, START_SCRIPT % {"width": WIDTH, "height": HEIGHT})
        await target.bring_to_front()
        await target.get(URL)
        await wait_for_clearance(browser, target)
        await asyncio.sleep(END_HOLD_SECONDS)
        recording = await run_script(recorder, STOP_SCRIPT)
    finally:
        browser.stop()

    with tempfile.TemporaryDirectory() as directory:
        video = Path(directory, "recording.webm")
        video.write_bytes(base64.b64decode(recording))
        video_to_gif(video, output)


async def main(name: str, schemes: list[str]) -> None:
    ASSETS.mkdir(exist_ok=True)
    for scheme in schemes:
        output = ASSETS / f"{name}-{scheme}.gif"
        await record(scheme, output)
        print(f"{output}: {output.stat().st_size // 1024} KB")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:] or ["light", "dark"]))
