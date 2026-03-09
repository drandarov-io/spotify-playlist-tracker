import json

from spotify_playlist_tracker.models import DiffReport, PlaylistEntry, PlaylistSnapshot
from spotify_playlist_tracker.storage import SnapshotStore


def test_snapshot_store_saves_and_loads_latest(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    snapshot = PlaylistSnapshot(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        fetched_at="2026-03-09T10:00:00Z",
        market="US",
        total_items=1,
        snapshot_id="snap-1",
        entries=(
            PlaylistEntry(
                position=0,
                item_type="track",
                spotify_id="track-1",
                uri="spotify:track:track-1",
                name="Song",
                artists=("Artist",),
            ),
        ),
    )

    path = store.save_snapshot(snapshot)
    loaded = store.load_latest_snapshot("playlist-1")

    assert path.exists()
    assert path.name == "2026-03-09T10-00-00Z_playlist_playlist-1_snapshot.json"
    assert loaded is not None
    assert loaded.playlist_id == snapshot.playlist_id
    assert loaded.entries[0].spotify_id == "track-1"


def test_snapshot_store_saves_diff(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    report = DiffReport(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        generated_at="2026-03-09T10:00:00Z",
        current_snapshot_at="2026-03-09T10:00:00Z",
        previous_snapshot_at=None,
        market="US",
        is_initial_run=True,
    )

    path = store.save_diff(report)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.exists()
    assert path.name == "2026-03-09T10-00-00Z_playlist_playlist-1_diff.json"
    assert payload["playlist_id"] == "playlist-1"


def test_snapshot_store_saves_summary_markdown(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    snapshot = PlaylistSnapshot(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        fetched_at="2026-03-09T10:00:00Z",
        market="US",
        total_items=1,
        entries=(
            PlaylistEntry(
                position=0,
                item_type="track",
                spotify_id="track-1",
                uri="spotify:track:track-1",
                name="Song",
                artists=("Artist",),
                is_playable=False,
                restriction_reason="market",
            ),
        ),
    )
    report = DiffReport(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        generated_at="2026-03-09T10:00:00Z",
        current_snapshot_at="2026-03-09T10:00:00Z",
        previous_snapshot_at=None,
        market="US",
        is_initial_run=True,
    )

    path = store.save_summary(report, snapshot)
    content = path.read_text(encoding="utf-8")

    assert path.exists()
    assert path.name == "2026-03-09T10-00-00Z_playlist_playlist-1_summary.md"
    assert "## Diff Summary" in content
    assert "## Currently Unavailable Songs" in content
    assert "configured market `US`" in content