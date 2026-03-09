import pytest

from spotify_playlist_tracker.settings import AppSettings, SettingsError


def test_settings_load_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_PLAYLIST_IDS", "playlist-a,playlist-b")
    monkeypatch.setenv("SPOTIFY_MARKET", "de")
    monkeypatch.setenv("SPOTIFY_INCLUDE_EPISODES", "true")
    monkeypatch.setenv("TRACKER_RESULTS_DIR", "results")
    monkeypatch.setenv("TRACKER_AUTH_FILE", ".auth")
    monkeypatch.setenv("TRACKER_SCHEDULE", "daily")
    monkeypatch.setenv("TRACKER_SUMMARY_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setenv("TRACKER_WEBHOOK_TIMEOUT_SECONDS", "12")

    settings = AppSettings.load(tmp_path)

    assert settings.playlists.playlist_ids == ("playlist-a", "playlist-b")
    assert settings.playlists.market == "DE"
    assert settings.playlists.include_episodes is True
    assert settings.paths.results_dir == (tmp_path / "results").resolve()
    assert settings.paths.token_file == (tmp_path / ".auth").resolve()
    assert settings.runtime.schedule == "daily"
    assert settings.runtime.summary_webhook_url == "https://example.test/hook"
    assert settings.runtime.webhook_timeout_seconds == 12.0


def test_settings_require_playlist_ids_for_check(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.delenv("SPOTIFY_PLAYLIST_IDS", raising=False)

    with pytest.raises(SettingsError):
        AppSettings.load(tmp_path)


def test_settings_allow_authorize_without_playlists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.delenv("SPOTIFY_PLAYLIST_IDS", raising=False)

    settings = AppSettings.load(tmp_path, require_playlists=False)

    assert settings.playlists.playlist_ids == ()


def test_settings_default_auth_file_can_live_in_state_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_PLAYLIST_IDS", "playlist-a")
    monkeypatch.setenv("TRACKER_AUTH_FILE", "state/.auth")

    settings = AppSettings.load(tmp_path)

    assert settings.paths.token_file == (tmp_path / "state" / ".auth").resolve()


def test_settings_default_schedule_is_daily(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_PLAYLIST_IDS", "playlist-a")
    monkeypatch.delenv("TRACKER_SCHEDULE", raising=False)

    settings = AppSettings.load(tmp_path)

    assert settings.runtime.schedule == "daily"