from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import DiffReport, LostTracks, PlaylistSnapshot


class SnapshotStore:
    def __init__(self, data_dir: Path) -> None:
        self._results_dir = data_dir

    def save_snapshot(self, snapshot: PlaylistSnapshot) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        target = self._results_dir / _build_filename(
            snapshot.fetched_at,
            snapshot.playlist_name,
            snapshot.playlist_id,
            "snapshot",
            ".json",
        )
        target.write_text(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def save_raw(self, fetched_at: str, playlist_name: str, playlist_id: str, payload: dict[str, Any]) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        target = self._results_dir / _build_filename(
            fetched_at,
            playlist_name,
            playlist_id,
            "raw",
            ".json",
        )
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def load_latest_snapshot(self, playlist_id: str) -> PlaylistSnapshot | None:
        files = self.list_snapshot_files(playlist_id)
        if not files:
            return None
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
        return PlaylistSnapshot.from_dict(payload)

    def list_snapshot_files(self, playlist_id: str) -> list[Path]:
        if not self._results_dir.exists():
            return []
        return sorted(self._results_dir.glob(f"*_{playlist_id}_snapshot.json"))

    def save_diff(self, report: DiffReport) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        target = self._results_dir / _build_filename(
            report.generated_at,
            report.playlist_name,
            report.playlist_id,
            "diff",
            ".json",
        )
        target.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def save_summary(
        self,
        report: DiffReport,
        snapshot: PlaylistSnapshot,
        hide_reordered: bool = False,
        lost_tracks: LostTracks | None = None,
    ) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        target = self._results_dir / _build_filename(
            report.generated_at,
            report.playlist_name,
            report.playlist_id,
            "summary",
            ".md",
        )
        target.write_text(
            report.format_markdown(snapshot, hide_reordered=hide_reordered, lost_tracks=lost_tracks),
            encoding="utf-8",
        )
        return target

    def save_unavailable_summary(self, generated_at: str, playlist_name: str, playlist_id: str, payload: dict[str, Any]) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        target = self._results_dir / _build_filename(
            generated_at,
            playlist_name,
            playlist_id,
            "unavailable_summary",
            ".json",
        )
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def save_unavailable_summary_markdown(self, generated_at: str, playlist_name: str, playlist_id: str, content: str) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        target = self._results_dir / _build_filename(
            generated_at,
            playlist_name,
            playlist_id,
            "unavailable_summary",
            ".md",
        )
        target.write_text(content, encoding="utf-8")
        return target


class LostMetadataStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def record(self, report: DiffReport) -> dict[str, dict[str, Any]]:
        tracks = self.load()
        changes = report.destructive_unavailable
        for change in changes:
            before = change.before or {}
            track = tracks.setdefault(
                before["spotify_id"],
                {
                    "name": before["name"],
                    "artists": before["artists"],
                    "album": before["album"],
                    "uri": before["uri"],
                    "last_seen_at": report.previous_snapshot_at,
                    "lost_at": report.current_snapshot_at,
                    "playlists": {},
                },
            )
            track["playlists"][report.playlist_id] = report.playlist_name

        if changes:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(tracks, indent=2, ensure_ascii=False), encoding="utf-8")
        return tracks


def _safe_timestamp(value: str) -> str:
    return value.replace(":", "-")


def _slugify_playlist_name(value: str) -> str:
    safe_chars = []
    for char in value.lower().strip():
        if char.isalnum():
            safe_chars.append(char)
        elif char in {" ", "-", "_"}:
            safe_chars.append("_")
    slug = "".join(safe_chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "playlist"


def _build_filename(timestamp: str, playlist_name: str, playlist_id: str, suffix: str, extension: str) -> str:
    return f"{_safe_timestamp(timestamp)}_{_slugify_playlist_name(playlist_name)}_{playlist_id}_{suffix}{extension}"
