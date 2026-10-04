"""Fetch a URL through Cloudflare: python -m cloudflare_skip <url>."""

import sys

from cloudflare_skip import get


def main() -> None:
    response = get(sys.argv[1])
    print(response.status_code)
    print(response.text[:500])


if __name__ == "__main__":
    main()
