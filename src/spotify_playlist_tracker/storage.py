from __future__ import annotations

import json
from pathlib import Path

from .models import DiffReport, PlaylistSnapshot


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
        target.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
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
        target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        return target

    def save_summary(self, report: DiffReport, snapshot: PlaylistSnapshot) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        target = self._results_dir / _build_filename(
            report.generated_at,
            report.playlist_name,
            report.playlist_id,
            "summary",
            ".md",
        )
        target.write_text(report.format_markdown(snapshot), encoding="utf-8")
        return target


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
