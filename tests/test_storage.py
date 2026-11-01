import json

from spotify_playlist_tracker.diff import compare_snapshots
from spotify_playlist_tracker.models import DiffReport, PlaylistEntry, PlaylistSnapshot
from spotify_playlist_tracker.storage import LostMetadataStore, SnapshotStore


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


def test_snapshot_store_saves_raw_payload(tmp_path) -> None:
    store = SnapshotStore(tmp_path)

    path = store.save_raw(
        "2026-03-09T10:00:00Z",
        "Playlist",
        "playlist-1",
        {"metadata": {"id": "playlist-1"}, "item_pages": [{"items": []}]},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.exists()
    assert path.name == "2026-03-09T10-00-00Z_playlist_playlist-1_raw.json"
    assert payload["metadata"]["id"] == "playlist-1"


def test_snapshot_store_saves_unavailable_summary_markdown(tmp_path) -> None:
    store = SnapshotStore(tmp_path)

    path = store.save_unavailable_summary_markdown(
        "2026-03-09T10:00:00Z",
        "Playlist",
        "playlist-1",
        "# Title\n",
    )

    assert path.exists()
    assert path.name == "2026-03-09T10-00-00Z_playlist_playlist-1_unavailable_summary.md"
    assert path.read_text(encoding="utf-8") == "# Title\n"
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


def test_snapshot_store_saves_unavailable_summary_json(tmp_path) -> None:
    store = SnapshotStore(tmp_path)

    path = store.save_unavailable_summary(
        "2026-03-09T10:00:00Z",
        "Playlist",
        "playlist-1",
        {"playlist_id": "playlist-1", "unavailable_count": 1, "items": []},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.exists()
    assert path.name == "2026-03-09T10-00-00Z_playlist_playlist-1_unavailable_summary.json"
    assert payload["unavailable_count"] == 1


def build_lost_metadata_report(
    playlist_id: str = "playlist-1",
    playlist_name: str = "Playlist",
    previous_at: str = "2026-03-09T00:00:00Z",
    current_at: str = "2026-03-10T00:00:00Z",
    lose_metadata: bool = True,
) -> DiffReport:
    before = PlaylistEntry(
        position=0,
        item_type="track",
        spotify_id="track-1",
        uri="spotify:track:track-1",
        name="Song",
        artists=("Artist", "Guest"),
        album="Album",
        is_playable=True,
    )
    after = (
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
        )
        if lose_metadata
        else before
    )

    def snapshot(fetched_at: str, entry: PlaylistEntry) -> PlaylistSnapshot:
        return PlaylistSnapshot(
            playlist_id=playlist_id,
            playlist_name=playlist_name,
            fetched_at=fetched_at,
            market="DE",
            total_items=1,
            entries=(entry,),
        )

    return compare_snapshots(snapshot(previous_at, before), snapshot(current_at, after))


def test_lost_metadata_store_load_returns_empty_when_file_is_missing(tmp_path) -> None:
    assert LostMetadataStore(tmp_path / "state" / "lost_metadata.json").load() == {}


def test_lost_metadata_store_records_tracks_that_lost_metadata(tmp_path) -> None:
    path = tmp_path / "state" / "lost_metadata.json"
    store = LostMetadataStore(path)

    recorded = store.record(build_lost_metadata_report())

    expected = {
        "track-1": {
            "name": "Song",
            "artists": ["Artist", "Guest"],
            "album": "Album",
            "uri": "spotify:track:track-1",
            "last_seen_at": "2026-03-09T00:00:00Z",
            "lost_at": "2026-03-10T00:00:00Z",
            "playlists": {"playlist-1": "Playlist"},
        }
    }
    assert recorded == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert store.load() == expected


def test_lost_metadata_store_keeps_first_loss_and_merges_playlists(tmp_path) -> None:
    store = LostMetadataStore(tmp_path / "lost_metadata.json")

    store.record(build_lost_metadata_report())
    recorded = store.record(
        build_lost_metadata_report(
            playlist_id="playlist-2",
            playlist_name="Other",
            previous_at="2026-03-10T00:00:00Z",
            current_at="2026-03-11T00:00:00Z",
        )
    )

    assert recorded["track-1"]["last_seen_at"] == "2026-03-09T00:00:00Z"
    assert recorded["track-1"]["lost_at"] == "2026-03-10T00:00:00Z"
    assert recorded["track-1"]["playlists"] == {"playlist-1": "Playlist", "playlist-2": "Other"}


def test_lost_metadata_store_does_not_write_without_lost_metadata(tmp_path) -> None:
    path = tmp_path / "state" / "lost_metadata.json"

    recorded = LostMetadataStore(path).record(build_lost_metadata_report(lose_metadata=False))

    assert recorded == {}
    assert not path.exists()


def test_snapshot_store_saves_summary_with_former_names_of_lost_tracks(tmp_path) -> None:
    report = build_lost_metadata_report()
    lost_tracks = LostMetadataStore(tmp_path / "lost_metadata.json").record(report)
    snapshot = PlaylistSnapshot(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        fetched_at="2026-03-10T00:00:00Z",
        market="DE",
        total_items=1,
        entries=(
            PlaylistEntry(
                position=0,
                item_type="track",
                spotify_id="track-1",
                uri="spotify:track:track-1",
                name=None,
                artists=(),
                is_playable=False,
                restriction_reason="market",
            ),
        ),
    )

    path = SnapshotStore(tmp_path).save_summary(report, snapshot, lost_tracks=lost_tracks)

    assert "| Unknown (Song) | Unknown (Artist, Guest) | market |" in path.read_text(encoding="utf-8")
