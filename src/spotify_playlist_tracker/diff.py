from __future__ import annotations

from collections import defaultdict

from .models import DiffChange, DiffReport, PlaylistEntry, PlaylistSnapshot, isoformat_now


def compare_snapshots(previous: PlaylistSnapshot | None, current: PlaylistSnapshot) -> DiffReport:
    if previous is None:
        return DiffReport(
            playlist_id=current.playlist_id,
            playlist_name=current.playlist_name,
            generated_at=isoformat_now(),
            current_snapshot_at=current.fetched_at,
            previous_snapshot_at=None,
            market=current.market,
            is_initial_run=True,
        )

    previous_map = _with_occurrence_keys(previous.entries)
    current_map = _with_occurrence_keys(current.entries)

    previous_keys = set(previous_map)
    current_keys = set(current_map)

    added_keys = sorted(current_keys - previous_keys)
    removed_keys = sorted(previous_keys - current_keys)
    matched_keys = sorted(previous_keys & current_keys)

    # Second pass: pair up orphaned adds/removes by (name, artists) so that
    # Spotify track relinkings show up as Changed rather than Remove + Add.
    relinked_pairs, added_keys, removed_keys = _match_by_name_artist(
        added_keys, removed_keys, current_map, previous_map,
    )

    added = tuple(_build_change(key, None, current_map[key]) for key in added_keys)
    removed = tuple(_build_change(key, previous_map[key], None) for key in removed_keys)

    reordered: list[DiffChange] = []
    changed: list[DiffChange] = []
    unavailable: list[DiffChange] = []

    for key in sorted(previous_keys & current_keys):
        before = previous_map[key]
        after = current_map[key]

        if before.position != after.position:
            reordered.append(_build_change(key, before, after))

        changed_fields = tuple(
            field_name
            for field_name in before.metadata().keys()
            if before.metadata()[field_name] != after.metadata()[field_name]
        )
        if changed_fields:
            changed.append(_build_change(key, before, after, changed_fields))

        if before.is_available and not after.is_available:
            unavailable.append(_build_change(key, before, after, changed_fields))

    # Process relinked pairs (matched by name+artists) the same way as
    # identity-matched entries so metadata changes are properly tracked.
    for prev_key, cur_key in relinked_pairs:
        before = previous_map[prev_key]
        after = current_map[cur_key]

        if before.position != after.position:
            reordered.append(_build_change(cur_key, before, after))

        changed_fields = tuple(
            field_name
            for field_name in before.metadata().keys()
            if before.metadata()[field_name] != after.metadata()[field_name]
        )
        if changed_fields:
            changed.append(_build_change(cur_key, before, after, changed_fields))

        if before.is_available and not after.is_available:
            unavailable.append(_build_change(cur_key, before, after, changed_fields))

    return DiffReport(
        playlist_id=current.playlist_id,
        playlist_name=current.playlist_name,
        generated_at=isoformat_now(),
        current_snapshot_at=current.fetched_at,
        previous_snapshot_at=previous.fetched_at,
        market=current.market,
        is_initial_run=False,
        added=added,
        removed=removed,
        reordered=tuple(reordered),
        changed=tuple(changed),
        unavailable=tuple(unavailable),
    )


def _with_occurrence_keys(entries: tuple[PlaylistEntry, ...]) -> dict[str, PlaylistEntry]:
    counters: dict[str, int] = defaultdict(int)
    keyed: dict[str, PlaylistEntry] = {}
    for entry in entries:
        counters[entry.base_identity] += 1
        occurrence = counters[entry.base_identity]
        keyed[f"{entry.base_identity}#{occurrence}"] = entry
    return keyed


def _name_artist_key(entry: PlaylistEntry) -> str | None:
    if not entry.name:
        return None
    artists = "|".join(entry.artists) if entry.artists else ""
    return f"{entry.name}\0{artists}"


def _match_by_name_artist(
    added_keys: list[str],
    removed_keys: list[str],
    current_map: dict[str, PlaylistEntry],
    previous_map: dict[str, PlaylistEntry],
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Pair orphaned added/removed entries that share the same name+artists.

    Returns (relinked_pairs, remaining_added, remaining_removed).
    """
    # Index removed entries by (name, artists)
    removed_by_na: dict[str, list[str]] = defaultdict(list)
    for key in removed_keys:
        na = _name_artist_key(previous_map[key])
        if na:
            removed_by_na[na].append(key)

    pairs: list[tuple[str, str]] = []
    still_added: list[str] = []
    consumed_removed: set[str] = set()

    for cur_key in added_keys:
        na = _name_artist_key(current_map[cur_key])
        if na and removed_by_na.get(na):
            prev_key = removed_by_na[na].pop(0)
            pairs.append((prev_key, cur_key))
            consumed_removed.add(prev_key)
        else:
            still_added.append(cur_key)

    still_removed = [k for k in removed_keys if k not in consumed_removed]
    return pairs, still_added, still_removed


def _build_change(
    key: str,
    before: PlaylistEntry | None,
    after: PlaylistEntry | None,
    changed_fields: tuple[str, ...] = (),
) -> DiffChange:
    return DiffChange(
        key=key,
        before_position=None if before is None else before.position,
        after_position=None if after is None else after.position,
        before=None if before is None else before.metadata(),
        after=None if after is None else after.metadata(),
        changed_fields=changed_fields,
    )
