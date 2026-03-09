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

    added = tuple(_build_change(key, None, current_map[key]) for key in sorted(current_keys - previous_keys))
    removed = tuple(_build_change(key, previous_map[key], None) for key in sorted(previous_keys - current_keys))

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
