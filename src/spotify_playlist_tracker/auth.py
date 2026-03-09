from __future__ import annotations

import json
import os
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .models import TokenData, utc_now
from .settings import AppSettings


TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SCOPES = "playlist-read-private playlist-read-collaborative"


class AuthError(RuntimeError):
    pass


class TokenStore:
    def __init__(self, token_file: Path) -> None:
        self._token_file = token_file

    def load(self) -> TokenData | None:
        if not self._token_file.exists():
            return None
        payload = json.loads(self._token_file.read_text(encoding="utf-8"))
        return TokenData.from_dict(payload)

    def save(self, token: TokenData) -> None:
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")

    @property
    def token_file(self) -> Path:
        return self._token_file


@dataclass
class AuthorizationResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None


def build_authorize_url(settings: AppSettings, state: str) -> str:
    settings.spotify.validate()
    params = {
        "response_type": "code",
        "client_id": settings.spotify.client_id,
        "redirect_uri": settings.spotify.redirect_uri,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def can_open_browser() -> bool:
    return os.getenv("DISPLAY") is not None or os.name == "nt" or os.name == "mac"


def _make_handler(result: AuthorizationResult) -> type[BaseHTTPRequestHandler]:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            result.code = query.get("code", [None])[0]
            result.state = query.get("state", [None])[0]
            result.error = query.get("error", [None])[0]

            body = b"Spotify authorization received. You can close this window."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return CallbackHandler


def _exchange_token(settings: AppSettings, form_data: dict[str, str]) -> TokenData:
    settings.spotify.validate()
    response = httpx.post(
        TOKEN_URL,
        data=form_data,
        auth=(settings.spotify.client_id, settings.spotify.client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    expires_in = int(payload["expires_in"])
    refresh_token = payload.get("refresh_token") or form_data.get("refresh_token")
    if not refresh_token:
        raise AuthError("Spotify token response did not include a refresh token.")
    return TokenData(
        access_token=str(payload["access_token"]),
        refresh_token=str(refresh_token),
        expires_at=utc_now().timestamp() + expires_in,
        scope=str(payload.get("scope", "")),
        token_type=str(payload.get("token_type", "Bearer")),
    )


def authorize(settings: AppSettings, timeout_seconds: int = 180) -> TokenData:
    redirect_uri = urlparse(settings.spotify.redirect_uri)
    if redirect_uri.scheme not in {"http", "https"}:
        raise AuthError("SPOTIFY_REDIRECT_URI must use http:// or https://.")

    if redirect_uri.hostname is None:
        raise AuthError("SPOTIFY_REDIRECT_URI must include a hostname.")

    if redirect_uri.port is None:
        raise AuthError("SPOTIFY_REDIRECT_URI must include an explicit port for the built-in authorize command.")

    state = secrets.token_urlsafe(24)
    authorize_url = build_authorize_url(settings, state)
    result = AuthorizationResult()
    bind_host = settings.runtime.auth_bind_host or _default_callback_bind_host(redirect_uri.hostname)
    server = HTTPServer((bind_host, redirect_uri.port), _make_handler(result))
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()

    print(f"Authorization callback listener: http://{bind_host}:{redirect_uri.port}/callback")
    print(f"Authorize URL: {authorize_url}")
    if can_open_browser():
        webbrowser.open(authorize_url)
    worker.join(timeout_seconds)
    server.server_close()

    if worker.is_alive():
        raise AuthError("Timed out waiting for Spotify authorization callback.")
    if result.error:
        raise AuthError(f"Spotify authorization failed: {result.error}")
    if not result.code:
        raise AuthError("Spotify authorization did not return an authorization code.")
    if result.state != state:
        raise AuthError("Spotify authorization state mismatch.")

    return _exchange_token(
        settings,
        {
            "grant_type": "authorization_code",
            "code": result.code,
            "redirect_uri": settings.spotify.redirect_uri,
        },
    )


def _default_callback_bind_host(hostname: str) -> str:
    if hostname in {"127.0.0.1", "localhost"}:
        return hostname
    return "0.0.0.0"


def refresh_access_token(settings: AppSettings, token: TokenData) -> TokenData:
    return _exchange_token(
        settings,
        {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        },
    )


def get_valid_token(settings: AppSettings, token_store: TokenStore) -> TokenData:
    token = token_store.load()
    if token is None:
        print(f"No Spotify token found at {token_store.token_file}.")
        print("Starting interactive authorization flow.")
        token = authorize(settings)
        token_store.save(token)
        print(f"Saved Spotify token to {token_store.token_file}")
        print(token_store.token_file.read_text(encoding='utf-8'))
        return token
    if token.is_expired():
        token = refresh_access_token(settings, token)
        token_store.save(token)
    return token
