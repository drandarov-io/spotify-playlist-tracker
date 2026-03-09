from spotify_playlist_tracker.auth import TokenStore, get_valid_token
from spotify_playlist_tracker.models import TokenData
from spotify_playlist_tracker.settings import AppSettings, PathConfig, PlaylistConfig, RuntimeConfig, SpotifyCredentials


def build_settings(tmp_path):
    return AppSettings(
        spotify=SpotifyCredentials(
            client_id="client",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8899/callback",
        ),
        playlists=PlaylistConfig(
            playlist_ids=("playlist-1",),
            market="DE",
            include_episodes=False,
        ),
        runtime=RuntimeConfig(
            schedule=None,
            summary_webhook_url=None,
            webhook_timeout_seconds=15.0,
            auth_bind_host=None,
        ),
        paths=PathConfig(
            root_dir=tmp_path,
            results_dir=tmp_path / "results",
            token_file=tmp_path / "state" / ".auth",
        ),
    )


def test_get_valid_token_bootstraps_missing_auth(monkeypatch, tmp_path, capsys) -> None:
    token = TokenData(
        access_token="access",
        refresh_token="refresh",
        expires_at=9999999999,
    )
    settings = build_settings(tmp_path)
    store = TokenStore(settings.paths.token_file)

    monkeypatch.setattr("spotify_playlist_tracker.auth.authorize", lambda settings: token)

    resolved = get_valid_token(settings, store)
    logged = capsys.readouterr().out

    assert resolved == token
    assert settings.paths.token_file.exists()
    assert "No Spotify token found at" in logged
    assert '"access_token": "access"' in logged