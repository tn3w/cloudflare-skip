from types import SimpleNamespace

import pytest

import cloudflare_skip

CLEARANCE = {"cookies": {"cf_clearance": "token"}, "user_agent": "agent"}


def make_response(status_code: int, mitigated: str | None = None):
    headers = {"cf-mitigated": mitigated} if mitigated else {}
    return SimpleNamespace(status_code=status_code, headers=headers)


@pytest.fixture(autouse=True)
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    monkeypatch.setattr(cloudflare_skip, "CACHE_FILE", path)
    return path


def test_is_challenge_reads_the_mitigation_header():
    assert cloudflare_skip.is_challenge(make_response(403, "challenge"))
    assert not cloudflare_skip.is_challenge(make_response(200))


def test_clearance_is_cached_per_host():
    cloudflare_skip.save_clearance("a.example", CLEARANCE)
    cloudflare_skip.save_clearance("b.example", CLEARANCE)

    cache = cloudflare_skip.load_cache()
    assert cache == {"a.example": CLEARANCE, "b.example": CLEARANCE}


def test_get_skips_the_browser_without_a_challenge(monkeypatch):
    monkeypatch.setattr(cloudflare_skip, "fetch", lambda *args: make_response(200))
    monkeypatch.setattr(cloudflare_skip, "solve_in_browser", pytest.fail)

    assert cloudflare_skip.get("https://a.example/").status_code == 200


def test_get_solves_once_then_reuses_the_cached_clearance(monkeypatch):
    solved = []

    async def solve_in_browser(url):
        solved.append(url)
        return CLEARANCE

    def fetch(url, clearance=None):
        return make_response(200) if clearance else make_response(403, "challenge")

    monkeypatch.setattr(cloudflare_skip, "fetch", fetch)
    monkeypatch.setattr(cloudflare_skip, "solve_in_browser", solve_in_browser)

    assert cloudflare_skip.get("https://a.example/").status_code == 200
    assert cloudflare_skip.get("https://a.example/").status_code == 200
    assert solved == ["https://a.example/"]
