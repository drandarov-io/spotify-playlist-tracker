from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

# Filename pattern: {timestamp}_{slug}_{id}_snapshot.json
_SNAPSHOT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T[\d-]+Z)_(.+?)_([A-Za-z0-9]+)_snapshot\.json$"
)


def discover_latest_snapshots(results_dir: Path) -> list[Path]:
    """Find the most recent snapshot file per playlist in *results_dir*."""
    # Group by (slug, playlist_id), pick the lexicographically last timestamp.
    best: dict[tuple[str, str], Path] = {}
    for p in sorted(results_dir.glob("*_snapshot.json")):
        m = _SNAPSHOT_RE.match(p.name)
        if not m:
            continue
        key = (m.group(2), m.group(3))
        best[key] = p  # sorted order ensures last = most recent
    return list(best.values())


def discover_latest_report(results_dir: Path) -> Path:
    reports = sorted(results_dir.glob("*_playlist_import_report.json"))
    if not reports:
        raise RuntimeError(f"No playlist import reports found in {results_dir}.")
    return reports[-1]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def parse_query(q: str) -> tuple[str, str]:
    m = re.search(r'track:"([^"]+)".*?artist:"([^"]+)"', q)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return q, ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-reference an import report against playlist snapshots and "
        "generate a markdown listing of missing tracks grouped by artist.",
    )
    parser.add_argument(
        "--payload", default="",
        help="Path to payload JSON. Optional — listen counts are read from the import report if available.",
    )
    parser.add_argument(
        "--report", default="",
        help="Path to a playlist import report JSON. If omitted, the latest report in --results-dir is used.",
    )
    parser.add_argument(
        "--snapshots", nargs="*", default=None,
        help="Snapshot JSON paths. If omitted, auto-discovers latest per playlist from --results-dir.",
    )
    parser.add_argument(
        "--results-dir", default="results",
        help="Base directory for auto-discovering reports and snapshots.",
    )
    parser.add_argument(
        "--output", default="",
        help="Output markdown path (default: <results-dir>/missing_tracks_by_artist.md).",
    )
    return parser.parse_args()


def load_playlist_entries(snapshot_paths: list[Path]) -> tuple[list[tuple[str, frozenset[str]]], dict[str, int]]:
    """Return (entries, playlist_counts) from snapshot files."""
    entries: list[tuple[str, frozenset[str]]] = []
    counts: dict[str, int] = {}
    for fpath in snapshot_paths:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        pname = data.get("playlist_name", fpath.stem)
        n = 0
        for entry in data.get("entries", []):
            name = normalize(entry.get("name") or "")
            artists = frozenset(normalize(a) for a in (entry.get("artists") or []) if a)
            entries.append((name, artists))
            n += 1
        counts[pname] = n
    return entries, counts


def classify_unresolved(
    unresolved: list[dict],
    playlist_entries: list[tuple[str, frozenset[str]]],
    listen_counts: dict[str, int],
) -> tuple[list[tuple[str, str]], list[tuple[str, str, int]]]:
    """Split unresolved items into (already_in_playlist, definitely_missing)."""
    matched: list[tuple[str, str]] = []
    missing: list[tuple[str, str, int]] = []

    for item in unresolved:
        q = item.get("query", "")
        track, artist = parse_query(q)
        t_norm = normalize(track)
        a_norm = normalize(artist)

        found = False
        for pname, partists in playlist_entries:
            if t_norm and t_norm in pname:
                if not a_norm or any(a_norm in pa for pa in partists):
                    found = True
                    break

        if found:
            matched.append((track, artist))
        else:
            missing.append((track, artist, listen_counts.get(q, 1)))

    return matched, missing


def generate_markdown(
    report_path: str,
    snapshot_names: list[str],
    matched_in_playlist: list[tuple[str, str]],
    definitely_missing: list[tuple[str, str, int]],
) -> str:
    by_artist: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for track, artist, lc in definitely_missing:
        key = artist.strip() if artist.strip() else "Unknown Artist"
        by_artist[key].append((track, lc))

    artist_totals = {a: sum(lc for _, lc in tracks) for a, tracks in by_artist.items()}
    sorted_artists = sorted(by_artist, key=lambda a: (-artist_totals[a], a.casefold()))

    playlist_label = " · ".join(f"**{n}**" for n in snapshot_names) if snapshot_names else "*(unknown)*"

    lines = [
        "# Missing Tracks — Not Found in Any Playlist",
        "",
        f"> Source: import report `{report_path}`  ",
        f"> Playlists checked: {playlist_label}  ",
        f"> Total missing: **{len(definitely_missing)}**  ",
        f"> Matched in playlists (excluded): **{len(matched_in_playlist)}**",
        "",
    ]

    if matched_in_playlist:
        lines += [
            "## Already in Playlists",
            "",
            "These entries from the unresolved list were found in a playlist via",
            "normalised substring matching and are excluded from the listing below.",
            "",
        ]
        for t, a in matched_in_playlist:
            lines.append(f"- *{t}* — {a}")
        lines += ["", "---", ""]

    lines += ["## Tracks by Artist", ""]

    for artist in sorted_artists:
        total = artist_totals[artist]
        lines.append(f"### {artist}")
        lines.append(f"*Total plays: {total}*")
        lines.append("")
        for track, lc in sorted(by_artist[artist], key=lambda x: (-x[1], x[0].casefold())):
            lines.append(f"- {track} *(×{lc})*")
        lines.append("")

    return "\n".join(lines)


_REPORT_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d-]+Z)_")


def _default_output_path(results_dir: Path, report_path: Path) -> Path:
    m = _REPORT_PREFIX_RE.match(report_path.name)
    prefix = f"{m.group(1)}_" if m else ""
    return results_dir / f"{prefix}missing_tracks_by_artist.md"


def main() -> None:
    args = parse_args()
    results_dir = Path(args.results_dir)

    # Resolve report.
    report_path = Path(args.report) if args.report else discover_latest_report(results_dir)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # Resolve snapshots.
    if args.snapshots is not None:
        snapshot_paths = [Path(s) for s in args.snapshots]
    else:
        snapshot_paths = discover_latest_snapshots(results_dir)
    if not snapshot_paths:
        raise RuntimeError(f"No snapshot files found in {results_dir}.")

    playlist_entries, playlist_counts = load_playlist_entries(snapshot_paths)

    print("Playlist track counts:")
    for k, v in playlist_counts.items():
        print(f"  {k}: {v}")
    print(f"Total playlist entries (with duplicates): {len(playlist_entries)}")

    unresolved = report.get("unresolved", [])

    # Build query → listen_count map from report unresolved entries (preferred),
    # falling back to payload if provided.
    listen_counts: dict[str, int] = {
        item["query"]: item.get("listen_count", 1)
        for item in unresolved
        if "query" in item
    }
    if args.payload:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        for c in payload.get("ordered_candidates", []):
            q = c.get("search_query", "")
            if q and q not in listen_counts:
                listen_counts[q] = c.get("listen_count", 1)

    print(f"\nUnresolved entries in report: {len(unresolved)}")

    matched, missing = classify_unresolved(unresolved, playlist_entries, listen_counts)

    print(f"Already in playlists: {len(matched)}")
    print(f"Definitely missing: {len(missing)}")

    md = generate_markdown(
        str(report_path),
        list(playlist_counts.keys()),
        matched,
        missing,
    )

    out = Path(args.output) if args.output else _default_output_path(results_dir, report_path)
    out.write_text(md, encoding="utf-8")
    print(f"\nDocument written to: {out}")


if __name__ == "__main__":
    main()
