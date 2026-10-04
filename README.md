<div align="center">

<p>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/tn3w/cloudflare-skip/master/assets/non-interactive-dark.gif">
    <img src="https://raw.githubusercontent.com/tn3w/cloudflare-skip/master/assets/non-interactive-light.gif" width="49%" alt="Non-interactive challenge">
  </picture>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/tn3w/cloudflare-skip/master/assets/interactive-dark.gif">
    <img src="https://raw.githubusercontent.com/tn3w/cloudflare-skip/master/assets/interactive-light.gif" width="49%" alt="Interactive challenge">
  </picture>
</p>

# cloudflare-skip

**Skip Cloudflare challenges: solve once in a real browser, then reuse the clearance
with fast HTTP requests.**

[![PyPI](https://img.shields.io/pypi/v/cloudflare-skip?color=1868f2)](https://pypi.org/project/cloudflare-skip)
[![Python](https://img.shields.io/pypi/pyversions/cloudflare-skip?color=1868f2)](https://pypi.org/project/cloudflare-skip)
[![License](https://img.shields.io/badge/license-Apache--2.0-1868f2)](https://github.com/tn3w/cloudflare-skip/blob/master/LICENSE)

</div>

## Quick start

```bash
pip install cloudflare-skip
cloudflare-skip https://cl-skip.tn3w.dev
```

```python
from cloudflare_skip import get

response = get("https://cl-skip.tn3w.dev", params={"q": "x"}, timeout=10)
print(response.status_code, response.text)
```

`get(url, **kwargs)` returns a `curl_cffi` response; keyword arguments (`params`,
`headers`, `cookies`, `timeout`, ...) go to `curl_cffi`. It raises `ChallengeError` if
Cloudflare still challenges after a solve and `TimeoutError` if the browser cannot solve
within 30s. The CLI prints the body, writes the status to stderr and exits 1 on 4xx/5xx.

Needs Chrome or Chromium and a display (headful; headless gets flagged).

## How it works

1. Request with `curl_cffi` (Chrome TLS fingerprint) and the cached clearance.
2. `cf-mitigated: challenge` → a real browser (`nodriver`) opens the page.
3. Interactive Turnstile: once its checkbox renders (detected through the frame's DOM,
   shadow roots included), `Tab` focuses it and `Space` ticks it.
4. Once `cf_clearance` exists and the challenge page is gone, cookies + user agent are
   cached per host in `~/.cache/cloudflare_skip.json` and replayed over HTTP.

| Run | Time |
|-----|------|
| First, browser solve (non-interactive) | ~2.9s |
| First, browser solve (interactive) | ~4.3s |
| Cached, in-process | ~0.02-0.1s |

## Notes

- The API is synchronous; from async code use `await asyncio.to_thread(get, url)`.
- A clearance is bound to the user agent and IP of the solving browser: `get` always
  sends the browser's user agent, and a proxy passed in `kwargs` makes the replay fail
  with `ChallengeError`. The cache holds session cookies, so it is created with mode 600.

- Turnstile rejected every CDP mouse click tried (bezier, wind and jitter paths,
  pressure 0.5, moves during the spinner, click before or after load). Keyboard passes.
- An earlier `cf_clearance` is issued before the final redirect and gets rejected, so
  the solve only finishes when the challenge page itself is gone.

## Development

```bash
uv run pytest
uv run scripts/record.py <name> [light|dark]   # re-record the header GIFs
```

`site/` is the test page behind https://cl-skip.tn3w.dev (GitHub Pages, deployed by
`pages.yml`), put behind a Cloudflare challenge rule.

`scripts/record.py` records Chrome via tab capture (`getDisplayMedia`) → `ffmpeg`
frames → `gifski`. Needs `ffmpeg` and `gifski`.
