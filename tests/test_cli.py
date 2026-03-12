import json

from spotify_playlist_tracker.cli import build_parser, run_check
from spotify_playlist_tracker.models import DiffReport, PlaylistEntry, PlaylistSnapshot, TokenData
from spotify_playlist_tracker.settings import AppSettings, PathConfig, PlaylistConfig, RuntimeConfig, SpotifyCredentials


def build_settings(tmp_path) -> AppSettings:
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
            schedule="daily",
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


def test_check_parser_accepts_raw_output_flag() -> None:
    parser = build_parser()

    args = parser.parse_args(["check", "--raw-output"])

    assert args.command == "check"
    assert args.raw_output is True


def test_checkunavailable_parser_selects_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["checkunavailable"])

    assert args.command == "checkunavailable"


def test_run_check_skips_diff_file_and_emits_raw_output_when_no_changes(monkeypatch, tmp_path, capsys) -> None:
    settings = build_settings(tmp_path)
    snapshot = PlaylistSnapshot(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        fetched_at="2026-03-10T00:00:00Z",
        market="DE",
        total_items=0,
        entries=(),
    )
    report = DiffReport(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        generated_at="2026-03-10T00:00:00Z",
        current_snapshot_at="2026-03-10T00:00:00Z",
        previous_snapshot_at="2026-03-09T00:00:00Z",
        market="DE",
        is_initial_run=False,
    )

    class FakeStore:
        def __init__(self, _results_dir):
            self.save_diff_called = False

        def load_latest_snapshot(self, _playlist_id):
            return snapshot

        def save_snapshot(self, _snapshot):
            return tmp_path / "results" / "snapshot.json"

        def save_raw(self, _fetched_at, _playlist_name, _playlist_id, _payload):
            return tmp_path / "results" / "raw.json"

        def save_diff(self, _report):
            self.save_diff_called = True
            return tmp_path / "results" / "diff.json"

        def save_summary(self, _report, _snapshot):
            return tmp_path / "results" / "summary.md"

    fake_store = FakeStore(tmp_path / "results")

    class FakeSpotifyClient:
        def __init__(self, _settings, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def fetch_playlist_data(self, _playlist_id):
            class Result:
                def __init__(self, snapshot):
                    self.snapshot = snapshot
                    self.raw_payload = {"metadata": {"id": snapshot.playlist_id}, "item_pages": []}

            return Result(snapshot)

    monkeypatch.setattr(
        "spotify_playlist_tracker.cli.get_valid_token",
        lambda settings, token_store: TokenData(
            access_token="access",
            refresh_token="refresh",
            expires_at=9999999999,
        ),
    )
    monkeypatch.setattr("spotify_playlist_tracker.cli.SnapshotStore", lambda _path: fake_store)
    monkeypatch.setattr("spotify_playlist_tracker.cli.SpotifyClient", FakeSpotifyClient)
    monkeypatch.setattr("spotify_playlist_tracker.cli.compare_snapshots", lambda previous, current: report)

    exit_code = run_check(settings, raw_output=True)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert fake_store.save_diff_called is False
    assert payload["playlist_id"] == "playlist-1"
    assert payload["files"]["snapshot"]["name"] == "snapshot.json"
    assert payload["files"]["raw"]["name"] == "raw.json"
    assert payload["files"]["diff"] is None
    assert payload["files"]["summary"] is None


def test_run_check_default_output_lists_files_without_console_summary(monkeypatch, tmp_path, capsys) -> None:
    settings = build_settings(tmp_path)
    snapshot = PlaylistSnapshot(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        fetched_at="2026-03-10T00:00:00Z",
        market="DE",
        total_items=0,
        entries=(),
    )
    report = DiffReport(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        generated_at="2026-03-10T00:00:00Z",
        current_snapshot_at="2026-03-10T00:00:00Z",
        previous_snapshot_at="2026-03-09T00:00:00Z",
        market="DE",
        is_initial_run=False,
    )

    class FakeStore:
        def __init__(self, _results_dir):
            pass

        def load_latest_snapshot(self, _playlist_id):
            return snapshot

        def save_snapshot(self, _snapshot):
            return tmp_path / "results" / "snapshot.json"

        def save_raw(self, _fetched_at, _playlist_name, _playlist_id, _payload):
            return tmp_path / "results" / "raw.json"

        def save_diff(self, _report):
            return tmp_path / "results" / "diff.json"

        def save_summary(self, _report, _snapshot):
            return tmp_path / "results" / "summary.md"

    class FakeSpotifyClient:
        def __init__(self, _settings, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def fetch_playlist_data(self, _playlist_id):
            class Result:
                def __init__(self, snapshot):
                    self.snapshot = snapshot
                    self.raw_payload = {"metadata": {"id": snapshot.playlist_id}, "item_pages": []}

            return Result(snapshot)

    monkeypatch.setattr(
        "spotify_playlist_tracker.cli.get_valid_token",
        lambda settings, token_store: TokenData(
            access_token="access",
            refresh_token="refresh",
            expires_at=9999999999,
        ),
    )
    monkeypatch.setattr("spotify_playlist_tracker.cli.SnapshotStore", lambda _path: FakeStore(_path))
    monkeypatch.setattr("spotify_playlist_tracker.cli.SpotifyClient", FakeSpotifyClient)
    monkeypatch.setattr("spotify_playlist_tracker.cli.compare_snapshots", lambda previous, current: report)

    exit_code = run_check(settings)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Snapshot:" in output
    assert "Raw:" in output
    assert "Compared" not in output
    assert "Added:" not in output


def test_run_checkunavailable_saves_only_unavailable_summary(monkeypatch, tmp_path, capsys) -> None:
    settings = build_settings(tmp_path)
    snapshot = PlaylistSnapshot(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        fetched_at="2026-03-10T00:00:00Z",
        market="DE",
        total_items=2,
        entries=(
            PlaylistEntry(
                position=0,
                item_type="track",
                spotify_id="track-1",
                uri="spotify:track:track-1",
                name=None,
                artists=(),
                album=None,
                is_playable=False,
                restriction_reason="market",
            ),
            PlaylistEntry(
                position=1,
                item_type="track",
                spotify_id="track-1",
                uri="spotify:track:track-1",
                name=None,
                artists=(),
                album=None,
                is_playable=False,
                restriction_reason="market",
            ),
        ),
    )

    class FakeStore:
        def __init__(self, _results_dir):
            self.saved_payload = None
            self.saved_markdown = None

        def save_snapshot(self, _snapshot):
            raise AssertionError("save_snapshot should not be called")

        def save_raw(self, *_args, **_kwargs):
            raise AssertionError("save_raw should not be called")

        def save_diff(self, _report):
            raise AssertionError("save_diff should not be called")

        def save_summary(self, _report, _snapshot):
            raise AssertionError("save_summary should not be called")

        def save_unavailable_summary(self, _generated_at, _playlist_name, _playlist_id, payload):
            self.saved_payload = payload
            return tmp_path / "results" / "unavailable_summary.json"

        def save_unavailable_summary_markdown(self, _generated_at, _playlist_name, _playlist_id, content):
            self.saved_markdown = content
            return tmp_path / "results" / "unavailable_summary.md"

    fake_store = FakeStore(tmp_path / "results")

    class FakeSpotifyClient:
        def __init__(self, _settings, _token):
            self.lookup_ids = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def fetch_playlist_snapshot(self, _playlist_id):
            return snapshot

        def fetch_tracks_metadata(self, track_ids):
            self.lookup_ids = list(track_ids)
            return {
                "track-1": {
                    "id": "track-1",
                    "name": "Recovered Song",
                    "artists": [{"name": "Recovered Artist"}],
                    "available_markets": ["US", "FR"],
                    "restrictions": {"reason": "market"},
                }
            }

    fake_client = FakeSpotifyClient(settings, "access")

    monkeypatch.setattr(
        "spotify_playlist_tracker.cli.get_valid_token",
        lambda settings, token_store: TokenData(
            access_token="access",
            refresh_token="refresh",
            expires_at=9999999999,
        ),
    )
    monkeypatch.setattr("spotify_playlist_tracker.cli.SnapshotStore", lambda _path: fake_store)
    monkeypatch.setattr("spotify_playlist_tracker.cli.SpotifyClient", lambda _settings, _token: fake_client)

    from spotify_playlist_tracker.cli import run_check_unavailable

    exit_code = run_check_unavailable(settings)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert fake_client.lookup_ids == ["track-1", "track-1"]
    assert fake_store.saved_payload is not None
    assert fake_store.saved_markdown is not None
    assert fake_store.saved_payload["unavailable_count"] == 2
    assert fake_store.saved_payload["lookup_track_count"] == 1
    assert fake_store.saved_payload["items"][0]["name"] == "Recovered Song"
    assert fake_store.saved_payload["items"][0]["market_lookup"]["available_market_count"] == 2
    assert "| URI | Name | Artists | Album | Available markets | Explanation |" in fake_store.saved_markdown
    assert "Recovered Song" in fake_store.saved_markdown
    assert "Unavailable summary:" in output
    assert "Unavailable summary markdown:" in output