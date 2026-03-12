from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TokenData:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str = ""
    token_type: str = "Bearer"

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return self.expires_at <= utc_now().timestamp() + skew_seconds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TokenData":
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            expires_at=float(payload["expires_at"]),
            scope=str(payload.get("scope", "")),
            token_type=str(payload.get("token_type", "Bearer")),
        )


@dataclass(frozen=True)
class PlaylistEntry:
    position: int
    item_type: str
    spotify_id: str | None
    uri: str | None
    name: str | None
    artists: tuple[str, ...] = ()
    album: str | None = None
    duration_ms: int | None = None
    explicit: bool | None = None
    is_local: bool = False
    added_at: str | None = None
    added_by: str | None = None
    is_playable: bool | None = None
    restriction_reason: str | None = None
    linked_from_id: str | None = None

    @property
    def availability_status(self) -> str:
        if self.restriction_reason:
            return self.restriction_reason
        if self.is_playable is False:
            return "unplayable"
        if self.name is None and self.spotify_id is None:
            return "missing"
        return "available"

    @property
    def is_available(self) -> bool:
        return self.availability_status == "available"

    @property
    def base_identity(self) -> str:
        if self.spotify_id:
            return f"spotify:{self.item_type}:{self.spotify_id}"
        artist_value = "|".join(self.artists) if self.artists else ""
        return f"fallback:{self.item_type}:{self.name or ''}:{artist_value}:{self.album or ''}:{self.duration_ms or ''}:{self.added_at or ''}"

    def metadata(self) -> dict[str, Any]:
        return {
            "item_type": self.item_type,
            "spotify_id": self.spotify_id,
            "uri": self.uri,
            "name": self.name,
            "artists": list(self.artists),
            "album": self.album,
            "duration_ms": self.duration_ms,
            "explicit": self.explicit,
            "is_local": self.is_local,
            "added_at": self.added_at,
            "added_by": self.added_by,
            "is_playable": self.is_playable,
            "restriction_reason": self.restriction_reason,
            "linked_from_id": self.linked_from_id,
            "availability_status": self.availability_status,
        }

    def availability_explanation(self, market: str) -> str:
        return _availability_explanation(
            self.availability_status,
            market,
            self.linked_from_id,
            self.is_playable,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artists"] = list(self.artists)
        payload["availability_status"] = self.availability_status
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PlaylistEntry":
        return cls(
            position=int(payload["position"]),
            item_type=str(payload.get("item_type", "track")),
            spotify_id=payload.get("spotify_id"),
            uri=payload.get("uri"),
            name=payload.get("name"),
            artists=tuple(payload.get("artists", [])),
            album=payload.get("album"),
            duration_ms=payload.get("duration_ms"),
            explicit=payload.get("explicit"),
            is_local=bool(payload.get("is_local", False)),
            added_at=payload.get("added_at"),
            added_by=payload.get("added_by"),
            is_playable=payload.get("is_playable"),
            restriction_reason=payload.get("restriction_reason"),
            linked_from_id=payload.get("linked_from_id"),
        )


@dataclass(frozen=True)
class PlaylistSnapshot:
    playlist_id: str
    playlist_name: str
    fetched_at: str
    market: str
    total_items: int
    snapshot_id: str | None = None
    entries: tuple[PlaylistEntry, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "fetched_at": self.fetched_at,
            "market": self.market,
            "total_items": self.total_items,
            "snapshot_id": self.snapshot_id,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PlaylistSnapshot":
        return cls(
            playlist_id=str(payload["playlist_id"]),
            playlist_name=str(payload.get("playlist_name", "")),
            fetched_at=str(payload["fetched_at"]),
            market=str(payload["market"]),
            total_items=int(payload["total_items"]),
            snapshot_id=payload.get("snapshot_id"),
            entries=tuple(PlaylistEntry.from_dict(item) for item in payload.get("entries", [])),
        )


@dataclass(frozen=True)
class DiffChange:
    key: str
    before_position: int | None
    after_position: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    changed_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "before_position": self.before_position,
            "after_position": self.after_position,
            "before": self.before,
            "after": self.after,
            "changed_fields": list(self.changed_fields),
        }


@dataclass(frozen=True)
class DiffReport:
    playlist_id: str
    playlist_name: str
    generated_at: str
    current_snapshot_at: str
    previous_snapshot_at: str | None
    market: str
    is_initial_run: bool
    added: tuple[DiffChange, ...] = ()
    removed: tuple[DiffChange, ...] = ()
    reordered: tuple[DiffChange, ...] = ()
    changed: tuple[DiffChange, ...] = ()
    unavailable: tuple[DiffChange, ...] = ()

    @property
    def has_changes(self) -> bool:
        return any((self.added, self.removed, self.reordered, self.changed, self.unavailable))

    @property
    def should_create_summary(self) -> bool:
        return self.should_create_summary_for(False)

    def should_create_summary_for(self, force_summary: bool = False) -> bool:
        return force_summary or self.is_initial_run or any((self.removed, self.reordered, self.changed, self.unavailable))

    def to_dict(self) -> dict[str, Any]:
        return {
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "generated_at": self.generated_at,
            "current_snapshot_at": self.current_snapshot_at,
            "previous_snapshot_at": self.previous_snapshot_at,
            "market": self.market,
            "is_initial_run": self.is_initial_run,
            "summary": {
                "added": len(self.added),
                "removed": len(self.removed),
                "reordered": len(self.reordered),
                "changed": len(self.changed),
                "unavailable": len(self.unavailable),
            },
            "added": [item.to_dict() for item in self.added],
            "removed": [item.to_dict() for item in self.removed],
            "reordered": [item.to_dict() for item in self.reordered],
            "changed": [item.to_dict() for item in self.changed],
            "unavailable": [item.to_dict() for item in self.unavailable],
        }

    def format_console(self) -> str:
        header = f"Playlist {self.playlist_name} ({self.playlist_id})"
        if self.is_initial_run:
            return f"{header}\n  Baseline snapshot saved at {self.current_snapshot_at}; no previous run to diff against."

        lines = [
            header,
            f"  Compared {self.previous_snapshot_at} -> {self.current_snapshot_at} in market {self.market}",
            f"  Added: {len(self.added)}",
            f"  Removed: {len(self.removed)}",
            f"  Reordered: {len(self.reordered)}",
            f"  Changed: {len(self.changed)}",
            f"  Unavailable: {len(self.unavailable)}",
        ]

        for label, changes in (
            ("Added", self.added[:5]),
            ("Removed", self.removed[:5]),
            ("Unavailable", self.unavailable[:5]),
            ("Changed", self.changed[:5]),
        ):
            for change in changes:
                item = change.after or change.before or {}
                name = item.get("name") or "Unknown"
                artists = ", ".join(item.get("artists", []))
                artist_suffix = f" - {artists}" if artists else ""
                lines.append(f"    {label}: {name}{artist_suffix}")

        return "\n".join(lines)

    def format_markdown(self, snapshot: PlaylistSnapshot) -> str:
        lines = [
            f"# Playlist Summary: {self.playlist_name}",
            "",
            f"- Playlist ID: `{self.playlist_id}`",
            f"- Market: `{self.market}`",
            f"- Current snapshot: `{self.current_snapshot_at}`",
        ]

        if self.previous_snapshot_at:
            lines.append(f"- Previous snapshot: `{self.previous_snapshot_at}`")
        else:
            lines.append("- Previous snapshot: none")

        lines.extend(
            [
                "",
                "## Diff Summary",
                "",
                "| Change Type | Count |",
                "| --- | ---: |",
                f"| Added | {len(self.added)} |",
                f"| Removed | {len(self.removed)} |",
                f"| Reordered | {len(self.reordered)} |",
                f"| Changed | {len(self.changed)} |",
                f"| Newly unavailable | {len(self.unavailable)} |",
                "",
            ]
        )

        if self.is_initial_run:
            lines.extend(
                [
                    "Initial run: baseline snapshot created, so there is no previous run to diff against.",
                    "",
                ]
            )
        elif not self.has_changes:
            lines.extend(
                [
                    "No differences were detected relative to the previous snapshot.",
                    "",
                ]
            )
        else:
            lines.extend(self._format_markdown_change_section("Added", self.added))
            lines.extend(self._format_markdown_change_section("Removed", self.removed))
            lines.extend(self._format_markdown_change_section("Reordered", self.reordered))
            lines.extend(self._format_markdown_change_section("Changed", self.changed, include_fields=True))
            lines.extend(self._format_markdown_change_section("Newly Unavailable", self.unavailable, include_reason=True))

        unavailable_entries = [entry for entry in snapshot.entries if not entry.is_available]
        lines.extend(
            [
                "## Currently Unavailable Songs",
                "",
            ]
        )
        if unavailable_entries:
            lines.extend(
                [
                    "| Song | Artists | Reason | Explanation |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for entry in unavailable_entries:
                song = _markdown_escape(entry.name or "Unknown")
                artists = _markdown_escape(", ".join(entry.artists) if entry.artists else "Unknown")
                reason = _markdown_escape(entry.availability_status)
                explanation = _markdown_escape(entry.availability_explanation(snapshot.market))
                lines.append(f"| {song} | {artists} | {reason} | {explanation} |")
        else:
            lines.append("No unavailable songs were found in the current snapshot.")

        lines.append("")
        return "\n".join(lines)

    def _format_markdown_change_section(
        self,
        title: str,
        changes: tuple[DiffChange, ...],
        *,
        include_fields: bool = False,
        include_reason: bool = False,
    ) -> list[str]:
        lines = [f"## {title}", ""]
        if not changes:
            lines.extend(["None.", ""])
            return lines

        for change in changes:
            item = change.after or change.before or {}
            name = _markdown_escape(item.get("name") or "Unknown")
            artists = _markdown_escape(", ".join(item.get("artists", [])) or "Unknown")
            detail_parts = [f"{name} - {artists}"]
            if change.before_position is not None or change.after_position is not None:
                detail_parts.append(
                    f"positions {change.before_position if change.before_position is not None else '-'} -> {change.after_position if change.after_position is not None else '-'}"
                )
            if include_fields and change.changed_fields:
                detail_parts.append("changes: " + "; ".join(_format_changed_field_values(change)))
            if include_reason:
                reason = item.get("availability_status") or item.get("restriction_reason") or "unknown"
                detail_parts.append(str(reason))
            lines.append(f"- {'; '.join(detail_parts)}")

        lines.append("")
        return lines


def _markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def _format_changed_field_values(change: DiffChange) -> list[str]:
    details: list[str] = []
    before = change.before or {}
    after = change.after or {}
    for field_name in change.changed_fields:
        before_value = _format_change_value(before.get(field_name))
        after_value = _format_change_value(after.get(field_name))
        details.append(
            f"{field_name}: {_markdown_escape(before_value)} -> {_markdown_escape(after_value)}"
        )
    return details


def _format_change_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    if value == "":
        return "empty"
    return str(value)


def _availability_explanation(reason: str, market: str, linked_from_id: str | None, is_playable: bool | None) -> str:
    if reason == "market":
        return f"The track is not playable in the configured market `{market}`."
    if reason == "product":
        return "The track is restricted for the current Spotify account product tier."
    if reason == "explicit":
        return "The track is blocked by the account's explicit-content setting."
    if reason == "unplayable":
        return "Spotify returned the track as not playable for the configured market or account context."
    if reason == "missing":
        return "Spotify returned incomplete item data, which usually means the catalog entry is unavailable or removed."
    if linked_from_id:
        return f"Spotify relinked the original track to another catalog item from source track `{linked_from_id}`."
    if is_playable is False:
        return "Spotify marked the item as not playable, but did not provide a specific restriction reason."
    return "Spotify reported that the item is unavailable, but did not include a more specific reason."


def isoformat_now() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
