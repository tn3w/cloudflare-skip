from types import SimpleNamespace

import pytest

import cloudflare_skip

CLEARANCE = {"cookies": {"cf_clearance": "token"}, "user_agent": "agent"}


def make_response(status_code: int, mitigated: str | None = None):
    headers = {"cf-mitigated": mitigated} if mitigated else {}
    return SimpleNamespace(status_code=status_code, headers=headers)


def fetch_challenged(*args, **kwargs):
    return make_response(403, "challenge")


def fetch_passed(*args, **kwargs):
    return make_response(200)


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


def test_corrupt_cache_is_ignored(cache_file):
    cache_file.write_text("{broken")

    assert cloudflare_skip.load_cache() == {}


def test_cache_is_readable_by_the_owner_only(cache_file):
    cloudflare_skip.save_clearance("a.example", CLEARANCE)

    assert cache_file.stat().st_mode & 0o777 == 0o600


def test_fetch_forces_the_clearance_user_agent_and_keeps_other_options(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        cloudflare_skip.session, "get", lambda url, **kwargs: sent.update(kwargs)
    )

    cloudflare_skip.fetch(
        "https://a.example/",
        CLEARANCE,
        headers={"User-Agent": "mine", "Accept": "text/html"},
        cookies={"session": "1"},
        params={"q": "x"},
    )

    assert sent["headers"] == {"User-Agent": "agent", "Accept": "text/html"}
    assert sent["cookies"] == {"session": "1", "cf_clearance": "token"}
    assert sent["params"] == {"q": "x"}


def test_get_raises_when_the_challenge_survives_the_solve(monkeypatch):
    async def solve_in_browser(url):
        return CLEARANCE

    monkeypatch.setattr(cloudflare_skip, "fetch", fetch_challenged)
    monkeypatch.setattr(cloudflare_skip, "solve_in_browser", solve_in_browser)

    with pytest.raises(cloudflare_skip.ChallengeError):
        cloudflare_skip.get("https://a.example/")


def test_get_skips_the_browser_without_a_challenge(monkeypatch):
    monkeypatch.setattr(cloudflare_skip, "fetch", fetch_passed)
    monkeypatch.setattr(cloudflare_skip, "solve_in_browser", pytest.fail)

    assert cloudflare_skip.get("https://a.example/").status_code == 200


def test_get_solves_once_then_reuses_the_cached_clearance(monkeypatch):
    solved = []

    async def solve_in_browser(url):
        solved.append(url)
        return CLEARANCE

    def fetch(url, clearance=None, **kwargs):
        return fetch_passed() if clearance else fetch_challenged()

    monkeypatch.setattr(cloudflare_skip, "fetch", fetch)
    monkeypatch.setattr(cloudflare_skip, "solve_in_browser", solve_in_browser)

    assert cloudflare_skip.get("https://a.example/").status_code == 200
    assert cloudflare_skip.get("https://a.example/").status_code == 200
    assert solved == ["https://a.example/"]
