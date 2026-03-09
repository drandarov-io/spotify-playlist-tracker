from spotify_playlist_tracker.diff import compare_snapshots
from spotify_playlist_tracker.models import PlaylistEntry, PlaylistSnapshot


def make_entry(position: int, spotify_id: str, *, name: str = "Song", playable: bool | None = True, restriction: str | None = None):
    return PlaylistEntry(
        position=position,
        item_type="track",
        spotify_id=spotify_id,
        uri=f"spotify:track:{spotify_id}",
        name=name,
        artists=("Artist",),
        album="Album",
        duration_ms=180000,
        explicit=False,
        is_local=False,
        added_at="2026-03-09T00:00:00Z",
        added_by="user",
        is_playable=playable,
        restriction_reason=restriction,
    )


def make_snapshot(*entries: PlaylistEntry, fetched_at: str = "2026-03-09T00:00:00Z") -> PlaylistSnapshot:
    return PlaylistSnapshot(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        fetched_at=fetched_at,
        market="US",
        total_items=len(entries),
        snapshot_id=None,
        entries=entries,
    )


def test_compare_snapshots_detects_add_remove_and_reorder() -> None:
    previous = make_snapshot(make_entry(0, "a"), make_entry(1, "b"))
    current = make_snapshot(make_entry(0, "b"), make_entry(1, "c"), fetched_at="2026-03-10T00:00:00Z")

    report = compare_snapshots(previous, current)

    assert len(report.added) == 1
    assert len(report.removed) == 1
    assert len(report.reordered) == 1
    assert report.added[0].after["spotify_id"] == "c"
    assert report.removed[0].before["spotify_id"] == "a"


def test_compare_snapshots_detects_unavailable_and_metadata_change() -> None:
    previous = make_snapshot(make_entry(0, "a", name="Old Name"))
    current = make_snapshot(
        make_entry(0, "a", name="New Name", playable=False, restriction="market"),
        fetched_at="2026-03-10T00:00:00Z",
    )

    report = compare_snapshots(previous, current)

    assert len(report.changed) == 1
    assert len(report.unavailable) == 1
    assert "name" in report.changed[0].changed_fields
    assert report.unavailable[0].after["availability_status"] == "market"


def test_compare_snapshots_handles_initial_run() -> None:
    current = make_snapshot(make_entry(0, "a"))

    report = compare_snapshots(None, current)

    assert report.is_initial_run is True
    assert not report.added


def test_added_only_diff_does_not_create_summary() -> None:
    previous = make_snapshot(make_entry(0, "a"))
    current = make_snapshot(
        make_entry(0, "a"),
        make_entry(1, "b"),
        fetched_at="2026-03-10T00:00:00Z",
    )

    report = compare_snapshots(previous, current)

    assert len(report.added) == 1
    assert report.should_create_summary is False


def test_removed_diff_creates_summary() -> None:
    previous = make_snapshot(make_entry(0, "a"), make_entry(1, "b"))
    current = make_snapshot(make_entry(0, "a"), fetched_at="2026-03-10T00:00:00Z")

    report = compare_snapshots(previous, current)

    assert len(report.removed) == 1
    assert report.should_create_summary is True


def test_force_summary_overrides_no_change_behavior() -> None:
    previous = make_snapshot(make_entry(0, "a"))
    current = make_snapshot(make_entry(0, "a"), fetched_at="2026-03-10T00:00:00Z")

    report = compare_snapshots(previous, current)

    assert report.has_changes is False
    assert report.should_create_summary is False
    assert report.should_create_summary_for(True) is True
