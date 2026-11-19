"""Failed-login throttling and API docs toggle."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.rate_limit import FailedLoginLimiter, login_limiter

GURU = "guru1@smkn1-pku.local"


def _attempt(client: TestClient, identifier: str, password: str):
    return client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )


def test_account_locked_after_repeated_failures(client: TestClient):
    limit = login_limiter.max_per_identifier
    for _ in range(limit):
        assert _attempt(client, GURU, "salah-sandi").status_code == 401
    blocked = _attempt(client, GURU, "salah-sandi")
    assert blocked.status_code == 429
    assert "Coba lagi" in blocked.json()["detail"]
    assert int(blocked.headers["Retry-After"]) > 0
    # Even the right password is refused while the lock is active.
    assert _attempt(client, GURU, "Password123!").status_code == 429
    # Identifier matching ignores case and surrounding spaces.
    assert _attempt(client, f"  {GURU.upper()} ", "Password123!").status_code == 429


def test_success_resets_counter(client: TestClient):
    limit = login_limiter.max_per_identifier
    for _ in range(limit - 1):
        assert _attempt(client, GURU, "salah-sandi").status_code == 401
    assert _attempt(client, GURU, "Password123!").status_code == 200
    for _ in range(limit - 1):
        assert _attempt(client, GURU, "salah-sandi").status_code == 401
    assert _attempt(client, GURU, "Password123!").status_code == 200


def test_unknown_accounts_count_against_identifier(client: TestClient):
    limit = login_limiter.max_per_identifier
    for _ in range(limit):
        assert _attempt(client, "tidak-ada@contoh.local", "x").status_code == 401
    assert _attempt(client, "tidak-ada@contoh.local", "x").status_code == 429
    # Other accounts are unaffected.
    assert _attempt(client, GURU, "Password123!").status_code == 200


def test_ip_limit_and_window_expiry(monkeypatch):
    limiter = FailedLoginLimiter(max_per_identifier=100, max_per_ip=3, window_seconds=60)
    clock = [1000.0]
    monkeypatch.setattr("app.core.rate_limit.time.monotonic", lambda: clock[0])
    for i in range(3):
        limiter.record_failure(f"user{i}", "10.0.0.1")
    assert limiter.retry_after("user-baru", "10.0.0.1") == 60
    assert limiter.retry_after("user-baru", "10.0.0.2") is None
    clock[0] += 61
    assert limiter.retry_after("user-baru", "10.0.0.1") is None


def test_api_docs_default_by_environment():
    assert Settings(APP_ENV="development").api_docs_enabled is True
    assert Settings(APP_ENV="test").api_docs_enabled is True
    assert Settings(APP_ENV="demo").api_docs_enabled is False
    assert Settings(APP_ENV="production").api_docs_enabled is False
    assert Settings(APP_ENV="demo", API_DOCS_ENABLED=True).api_docs_enabled is True
