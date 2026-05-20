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
_REPORT_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d-]+Z)_")


def discover_latest_snapshots(results_dir: Path) -> list[Path]:
    """Find the most recent snapshot file per playlist in *results_dir*."""
    best: dict[tuple[str, str], Path] = {}
    for path in sorted(results_dir.glob("*_snapshot.json")):
        match = _SNAPSHOT_RE.match(path.name)
        if not match:
            continue
        key = (match.group(2), match.group(3))
        best[key] = path
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


def parse_query(query: str) -> tuple[str, str]:
    match = re.search(r'track:"([^"]+)".*?artist:"([^"]+)"', query)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return query, ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-reference an import report against playlist snapshots and generate a markdown listing of missing tracks grouped by artist.",
    )
    parser.add_argument(
        "--payload",
        default="",
        help="Path to payload JSON. Optional — listen counts are read from the import report if available.",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Path to a playlist import report JSON. If omitted, the latest report in --results-dir is used.",
    )
    parser.add_argument(
        "--snapshots",
        nargs="*",
        default=None,
        help="Snapshot JSON paths. If omitted, auto-discovers latest per playlist from --results-dir.",
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Base directory for auto-discovering reports and snapshots.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output markdown path (default: <results-dir>/missing_tracks_by_artist.md).",
    )
    return parser.parse_args()


def load_playlist_entries(snapshot_paths: list[Path]) -> tuple[list[tuple[str, frozenset[str]]], dict[str, int]]:
    """Return (entries, playlist_counts) from snapshot files."""
    entries: list[tuple[str, frozenset[str]]] = []
    counts: dict[str, int] = {}
    for path in snapshot_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        playlist_name = data.get("playlist_name", path.stem)
        count = 0
        for entry in data.get("entries", []):
            name = normalize(entry.get("name") or "")
            artists = frozenset(normalize(artist) for artist in (entry.get("artists") or []) if artist)
            entries.append((name, artists))
            count += 1
        counts[playlist_name] = count
    return entries, counts


def display_artist(value: str) -> str:
    return value.strip() if value.strip() else "Unknown Artist"


def format_match_label(match: dict | None) -> str:
    if not match:
        return "none"

    track_name = str(match.get("track_name") or "").strip()
    artists = str(match.get("artists") or "").strip()
    if track_name and artists:
        return f"{track_name} — {artists}"
    if track_name:
        return track_name
    if artists:
        return artists

    query = str(match.get("query") or "").strip()
    return query or "none"


def build_missing_entry(item: dict, listen_counts: dict[str, int]) -> dict[str, object]:
    query = str(item.get("query", ""))
    track, artist = parse_query(query)
    highest_scored_match = item.get("highest_scored_match")
    return {
        "track": track,
        "artist": artist,
        "listen_count": listen_counts.get(query, 1),
        "highest_score": item.get("highest_score", 0),
        "highest_score_match": format_match_label(highest_scored_match if isinstance(highest_scored_match, dict) else None),
    }


def classify_unresolved(
    unresolved: list[dict],
    playlist_entries: list[tuple[str, frozenset[str]]],
    listen_counts: dict[str, int],
) -> tuple[list[tuple[str, str]], list[dict[str, object]]]:
    """Split unresolved items into (already_in_playlist, definitely_missing)."""
    matched: list[tuple[str, str]] = []
    missing: list[dict[str, object]] = []

    for item in unresolved:
        query = str(item.get("query", ""))
        track, artist = parse_query(query)
        track_normalized = normalize(track)
        artist_normalized = normalize(artist)

        found = False
        for playlist_track_name, playlist_artists in playlist_entries:
            if track_normalized and track_normalized in playlist_track_name:
                if not artist_normalized or any(artist_normalized in playlist_artist for playlist_artist in playlist_artists):
                    found = True
                    break

        if found:
            matched.append((track, artist))
        else:
            missing.append(build_missing_entry(item, listen_counts))

    return matched, missing


def collect_unresolved_as_missing(unresolved: list[dict], listen_counts: dict[str, int]) -> list[dict[str, object]]:
    return [build_missing_entry(item, listen_counts) for item in unresolved]


def collect_low_confidence_matches(resolved: list[dict], threshold: int = 70) -> list[dict[str, object]]:
    low_confidence_matches: list[dict[str, object]] = []
    for item in resolved:
        score = item.get("score")
        if not isinstance(score, int) or score > threshold:
            continue

        track, artist = parse_query(str(item.get("query", "")))
        low_confidence_matches.append(
            {
                "track": track,
                "artist": artist,
                "listen_count": item.get("listen_count", 1),
                "score": score,
                "match": format_match_label(item),
            }
        )
    return low_confidence_matches


def group_by_artist(entries: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        grouped[display_artist(str(entry.get("artist", "")))].append(entry)
    return grouped


def artist_totals(grouped: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    return {
        artist: sum(int(entry.get("listen_count", 0)) for entry in items)
        for artist, items in grouped.items()
    }


def pad_listen_count(value: object) -> str:
    return f"{int(value):03d}"


def pad_score(value: object) -> str:
    return f"{int(value):02d}"


def generate_markdown(
    report_path: str,
    snapshot_names: list[str],
    matched_in_playlist: list[tuple[str, str]],
    definitely_missing: list[dict[str, object]],
    low_confidence_matches: list[dict[str, object]],
) -> str:
    missing_by_artist = group_by_artist(definitely_missing)
    low_confidence_by_artist = group_by_artist(low_confidence_matches)

    missing_totals = artist_totals(missing_by_artist)
    low_confidence_totals = artist_totals(low_confidence_by_artist)

    sorted_missing_artists = sorted(missing_by_artist, key=lambda artist: (-missing_totals[artist], artist.casefold()))
    sorted_low_confidence_artists = sorted(
        low_confidence_by_artist,
        key=lambda artist: (-low_confidence_totals[artist], artist.casefold()),
    )

    playlist_label = " · ".join(f"**{name}**" for name in snapshot_names) if snapshot_names else "*(report-only mode)*"

    lines = [
        "# Missing Tracks — Not Found in Any Playlist",
        "",
        f"> Source: import report `{report_path}`  ",
        f"> Playlists checked: {playlist_label}  ",
        f"> Total missing: **{len(definitely_missing)}**  ",
        f"> Low-confidence matched (score <= 70): **{len(low_confidence_matches)}**  ",
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
        for track, artist in matched_in_playlist:
            lines.append(f"- *{track}* — {artist}")
        lines += ["", "---", ""]

    lines += ["## Tracks by Artist", ""]

    for artist in sorted_missing_artists:
        lines.append(f"### {artist}")
        lines.append(f"*Total plays: {missing_totals[artist]}*")
        lines.append("")
        for entry in sorted(
            missing_by_artist[artist],
            key=lambda item: (-int(item.get("listen_count", 0)), str(item.get("track", "")).casefold()),
        ):
            lines.append(f"- {entry.get('track', '')}")
            lines.append(
                f"    - *×{pad_listen_count(entry.get('listen_count', 0))} / highest score: {pad_score(entry.get('highest_score', 0))} / {entry.get('highest_score_match', 'none')}*"
            )
        lines.append("")

    if low_confidence_matches:
        lines += ["---", "", "## Low-Confidence Matches", "", "These tracks were matched and added, but only with a score of 70 or below.", ""]
        for artist in sorted_low_confidence_artists:
            lines.append(f"### {artist}")
            lines.append(f"*Total plays: {low_confidence_totals[artist]}*")
            lines.append("")
            for entry in sorted(
                low_confidence_by_artist[artist],
                key=lambda item: (-int(item.get("listen_count", 0)), str(item.get("track", "")).casefold()),
            ):
                lines.append(f"- {entry.get('track', '')}")
                lines.append(
                    f"    - *×{pad_listen_count(entry.get('listen_count', 0))} / score: {pad_score(entry.get('score', 0))} / {entry.get('match', 'none')}*"
                )
            lines.append("")

    return "\n".join(lines)


def default_output_path(results_dir: Path, report_path: Path) -> Path:
    match = _REPORT_PREFIX_RE.match(report_path.name)
    prefix = f"{match.group(1)}_" if match else ""
    return results_dir / f"{prefix}missing_tracks_by_artist.md"


def main() -> None:
    args = parse_args()
    results_dir = Path(args.results_dir)

    report_path = Path(args.report) if args.report else discover_latest_report(results_dir)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    report_only_mode = bool(args.report) and args.snapshots is None
    if report_only_mode:
        snapshot_paths = []
    elif args.snapshots is not None:
        snapshot_paths = [Path(path) for path in args.snapshots]
    else:
        snapshot_paths = discover_latest_snapshots(results_dir)

    playlist_entries: list[tuple[str, frozenset[str]]] = []
    playlist_counts: dict[str, int] = {}
    if snapshot_paths:
        playlist_entries, playlist_counts = load_playlist_entries(snapshot_paths)
        print("Playlist track counts:")
        for name, count in playlist_counts.items():
            print(f"  {name}: {count}")
        print(f"Total playlist entries (with duplicates): {len(playlist_entries)}")
    else:
        print("No snapshot files provided or discovered. Running in report-only mode.")

    unresolved = report.get("unresolved", [])
    resolved = report.get("resolved", [])

    listen_counts: dict[str, int] = {
        item["query"]: item.get("listen_count", 1)
        for item in unresolved
        if "query" in item
    }
    if args.payload:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        for candidate in payload.get("ordered_candidates", []):
            query = candidate.get("search_query", "")
            if query and query not in listen_counts:
                listen_counts[query] = candidate.get("listen_count", 1)

    print(f"\nUnresolved entries in report: {len(unresolved)}")

    if playlist_entries:
        matched, missing = classify_unresolved(unresolved, playlist_entries, listen_counts)
    else:
        matched = []
        missing = collect_unresolved_as_missing(unresolved, listen_counts)

    low_confidence_matches = collect_low_confidence_matches(resolved)

    print(f"Already in playlists: {len(matched)}")
    print(f"Definitely missing: {len(missing)}")
    print(f"Low-confidence matched (<= 70): {len(low_confidence_matches)}")

    markdown = generate_markdown(
        str(report_path),
        list(playlist_counts.keys()),
        matched,
        missing,
        low_confidence_matches,
    )

    output_path = Path(args.output) if args.output else default_output_path(results_dir, report_path)
    output_path.write_text(markdown, encoding="utf-8")
    print(f"\nDocument written to: {output_path}")


if __name__ == "__main__":
    main()
