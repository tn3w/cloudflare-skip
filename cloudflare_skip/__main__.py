"""Fetch a URL through Cloudflare: python -m cloudflare_skip <url>."""

import argparse
import sys

from cloudflare_skip import get


def main() -> None:
    parser = argparse.ArgumentParser(prog="cloudflare-skip", description=__doc__)
    parser.add_argument("url")
    response = get(parser.parse_args().url)
    print(response.text)
    print(response.status_code, file=sys.stderr)
    raise SystemExit(response.status_code >= 400)


if __name__ == "__main__":
    main()
