from __future__ import annotations

from jobtomail.services import google_oauth


def test_build_auth_url_includes_required_scopes(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://app.example.com/auth/google/callback")

    url = google_oauth.build_auth_url(state="abc123")

    assert "client_id=test-client-id" in url
    assert "state=abc123" in url
    assert "gmail.send" in url
    assert "openid" in url
    assert "access_type=offline" in url  # required to receive a refresh_token
