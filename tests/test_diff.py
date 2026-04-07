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
    previous = make_snapshot(make_entry(0, "a", name="Alpha"), make_entry(1, "b"))
    current = make_snapshot(make_entry(0, "b"), make_entry(1, "c", name="Gamma"), fetched_at="2026-03-10T00:00:00Z")

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


def test_format_markdown_shows_exact_changed_values() -> None:
    previous = make_snapshot(make_entry(254, "a", name="Before Song", playable=True))
    current = make_snapshot(
        make_entry(254, "a", name="Before Song", playable=False, restriction="market"),
        fetched_at="2026-03-10T00:00:00Z",
    )

    report = compare_snapshots(previous, current)
    content = report.format_markdown(current)

    assert "positions 254 -> 254" in content
    assert "is_playable: true -> false" in content
    assert "restriction_reason: none -> market" in content
    assert "availability_status: available -> market" in content


def test_format_markdown_shows_exact_artist_list_change() -> None:
    previous = make_snapshot(make_entry(141, "a", name="Song"))
    current_entry = PlaylistEntry(
        position=141,
        item_type="track",
        spotify_id="a",
        uri="spotify:track:a",
        name="Song",
        artists=("Artist One", "Artist Two"),
        album="Album",
        duration_ms=180000,
        explicit=False,
        is_local=False,
        added_at="2026-03-09T00:00:00Z",
        added_by="user",
        is_playable=True,
        restriction_reason=None,
    )
    current = make_snapshot(current_entry, fetched_at="2026-03-10T00:00:00Z")

    report = compare_snapshots(previous, current)
    content = report.format_markdown(current)

    assert "artists: Artist -> Artist One, Artist Two" in content


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


def test_relinked_track_shows_as_changed_not_added_removed() -> None:
    """When Spotify relinks a track to a new ID but name+artists stay the same,
    it should appear as Changed (not as a Remove + Add pair)."""
    previous = make_snapshot(
        make_entry(0, "old-id", name="No Pasaran!!!", playable=False, restriction="market"),
    )
    current = make_snapshot(
        make_entry(0, "new-id", name="No Pasaran!!!", playable=True),
        fetched_at="2026-03-10T00:00:00Z",
    )

    report = compare_snapshots(previous, current)

    assert len(report.added) == 0
    assert len(report.removed) == 0
    assert len(report.changed) == 1
    assert "spotify_id" in report.changed[0].changed_fields
    assert report.changed[0].before["spotify_id"] == "old-id"
    assert report.changed[0].after["spotify_id"] == "new-id"


def test_relinked_track_detected_as_unavailable_when_applicable() -> None:
    """A relinked track that transitions to unavailable should also appear in unavailable."""
    previous = make_snapshot(
        make_entry(0, "old-id", name="Song", playable=True),
    )
    current = make_snapshot(
        make_entry(0, "new-id", name="Song", playable=False, restriction="market"),
        fetched_at="2026-03-10T00:00:00Z",
    )

    report = compare_snapshots(previous, current)

    assert len(report.added) == 0
    assert len(report.removed) == 0
    assert len(report.changed) == 1
    assert len(report.unavailable) == 1


def test_different_name_different_id_still_shows_as_added_removed() -> None:
    """Tracks with different IDs AND different names should remain as Add + Remove."""
    previous = make_snapshot(make_entry(0, "old-id", name="Old Song"))
    current = make_snapshot(
        make_entry(0, "new-id", name="New Song"),
        fetched_at="2026-03-10T00:00:00Z",
    )

    report = compare_snapshots(previous, current)

    assert len(report.added) == 1
    assert len(report.removed) == 1
    assert len(report.changed) == 0
