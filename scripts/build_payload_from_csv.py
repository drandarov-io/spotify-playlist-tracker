from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TypedDict, cast


class CandidateAggregate(TypedDict):
    artist: str
    track: str
    listen_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an ordered_candidates payload JSON from a Last.fm recenttracks CSV over a UTC date range.",
    )
    parser.add_argument(
        "--csv",
        dest="csv_paths",
        action="append",
        required=True,
        help="Path to a recenttracks CSV export. Repeat --csv to combine multiple exports.",
    )
    parser.add_argument("--start-date", required=True, help="Inclusive UTC start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", required=True, help="Inclusive UTC end date in YYYY-MM-DD format.")
    parser.add_argument("--start-time", default="", help="Optional inclusive UTC start time in HH:MM format.")
    parser.add_argument("--end-time", default="", help="Optional inclusive UTC end time in HH:MM format.")
    parser.add_argument(
        "--exclude-playlist-file",
        dest="exclude_playlist_files",
        action="append",
        default=[],
        help="Snapshot or raw playlist JSON file whose tracks should be excluded. Repeat for multiple files.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path. Defaults to results/<start>_to_<end>_<csv-stem>_playlist_payload.json.",
    )
    return parser.parse_args()


def parse_hhmm(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except ValueError as error:
        raise ValueError(f"Invalid time '{value}'. Expected HH:MM in 24-hour format.") from error
    return parsed


def normalize_field(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"\s+", " ", normalized)


def escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_output_path(csv_paths: list[Path], start_date: date, end_date: date) -> Path:
    stem = csv_paths[0].stem if len(csv_paths) == 1 else "merged_recenttracks"
    return Path("results") / f"{start_date.isoformat()}_to_{end_date.isoformat()}_{stem}_playlist_payload.json"


def is_within_time_window(current: time, start_time: time, end_time: time) -> bool:
    if start_time <= end_time:
        return start_time <= current <= end_time
    return current >= start_time or current <= end_time


def build_playlist_exclusion_pairs(playlist_file_paths: list[Path]) -> set[tuple[str, str]]:
    excluded_pairs: set[tuple[str, str]] = set()

    for playlist_file_path in playlist_file_paths:
        data = json.loads(playlist_file_path.read_text(encoding="utf-8"))

        if isinstance(data.get("entries"), list):
            for entry in data["entries"]:
                if not isinstance(entry, dict):
                    continue
                track = normalize_field(str(entry.get("name") or ""))
                artists = [normalize_field(str(artist or "")) for artist in entry.get("artists") or []]
                if not track:
                    continue
                excluded_pairs.update((artist, track) for artist in artists if artist)
            continue

        for page in data.get("item_pages") or []:
            if not isinstance(page, dict):
                continue
            for item in page.get("items") or []:
                if not isinstance(item, dict):
                    continue
                track_payload = item.get("track")
                if not isinstance(track_payload, dict) or track_payload.get("type") != "track":
                    continue
                track = normalize_field(str(track_payload.get("name") or ""))
                artists = [
                    normalize_field(str(artist.get("name") or ""))
                    for artist in track_payload.get("artists") or []
                    if isinstance(artist, dict)
                ]
                if not track:
                    continue
                excluded_pairs.update((artist, track) for artist in artists if artist)

    return excluded_pairs


def build_payload(
    csv_paths: list[Path],
    start_date: date,
    end_date: date,
    start_time: time | None = None,
    end_time: time | None = None,
    exclude_playlist_files: list[Path] | None = None,
) -> dict[str, object]:
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    start_unix = int(start_at.timestamp())
    end_exclusive_unix = int(end_exclusive.timestamp())

    exclude_playlist_files = exclude_playlist_files or []
    excluded_pairs = build_playlist_exclusion_pairs(exclude_playlist_files)

    grouped: dict[tuple[str, str], CandidateAggregate] = {}
    input_rows = 0
    filtered_rows = 0
    excluded_rows = 0

    for csv_path in csv_paths:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                input_rows += 1
                uts = int(row.get("uts") or 0)
                if uts < start_unix or uts >= end_exclusive_unix:
                    continue

                if start_time is not None and end_time is not None:
                    current_time = datetime.fromtimestamp(uts, tz=timezone.utc).time().replace(tzinfo=None)
                    if not is_within_time_window(current_time, start_time, end_time):
                        continue

                artist = (row.get("artist") or "").strip()
                track = (row.get("track") or "").strip()
                if not artist or not track:
                    continue

                artist_key = normalize_field(artist)
                track_key = normalize_field(track)
                if not artist_key or not track_key:
                    continue

                if (artist_key, track_key) in excluded_pairs:
                    excluded_rows += 1
                    continue

                filtered_rows += 1
                key = (artist_key, track_key)
                candidate = grouped.get(key)
                if candidate is None:
                    candidate = CandidateAggregate(artist=artist, track=track, listen_count=0)
                    grouped[key] = candidate
                candidate["listen_count"] += 1

    ordered_candidates: list[dict[str, object]] = []
    for position, candidate in enumerate(
        sorted(
            grouped.values(),
            key=lambda item: (-int(item["listen_count"]), str(item["artist"]).casefold(), str(item["track"]).casefold()),
        ),
        start=1,
    ):
        artist = str(candidate["artist"])
        track = str(candidate["track"])
        ordered_candidates.append(
            {
                "position": position,
                "artist": artist,
                "track": track,
                "listen_count": int(candidate["listen_count"]),
                "search_query": f'track:"{escape_query_value(track)}" artist:"{escape_query_value(artist)}"',
            }
        )

    return {
        "note": (
            "This file contains the full ordered candidate list derived from recenttracks rows "
            "within the provided UTC date range"
            f"{'' if start_time is None or end_time is None else ' and UTC time window'}"
            f"{'' if not exclude_playlist_files else ', excluding tracks already present in the provided playlist files'}"
            ". It is formatted for scripts/create_playlist_from_payload.py."
        ),
        "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
        "source_files": {
            **(
                {"recenttracks_csv": str(csv_paths[0]).replace("/", "\\")}
                if len(csv_paths) == 1
                else {"recenttracks_csvs": [str(csv_path).replace("/", "\\") for csv_path in csv_paths]}
            ),
            **(
                {}
                if not exclude_playlist_files
                else {"excluded_playlist_files": [str(path).replace("/", "\\") for path in exclude_playlist_files]}
            ),
        },
        "date_range_utc": {
            "start": iso_utc(start_at),
            "end": iso_utc(end_exclusive - timedelta(seconds=1)),
        },
        **(
            {}
            if start_time is None or end_time is None
            else {"time_range_utc": {"start": start_time.strftime("%H:%M"), "end": end_time.strftime("%H:%M")}}
        ),
        "matching_method": {
            "type": "exact_normalized_artist_plus_track",
            "normalization": [
                "Unicode NFKC normalization",
                "trim",
                "lowercase",
                "collapse internal whitespace",
            ],
        },
        "summary": {
            "input_recenttracks_rows": input_rows,
            "filtered_recenttracks_rows": filtered_rows,
            "excluded_recenttracks_rows": excluded_rows,
            "excluded_playlist_artist_track_pairs": len(excluded_pairs),
            "unique_artist_track_pairs": len(ordered_candidates),
        },
        "ordered_candidates": ordered_candidates,
    }


def main() -> None:
    args = parse_args()
    csv_paths = [Path(value) for value in args.csv_paths]
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if bool(args.start_time) != bool(args.end_time):
        raise ValueError("--start-time and --end-time must be provided together.")
    start_time = parse_hhmm(args.start_time) if args.start_time else None
    end_time = parse_hhmm(args.end_time) if args.end_time else None
    output_path = Path(args.output) if args.output else default_output_path(csv_paths, start_date, end_date)

    payload = build_payload(
        csv_paths,
        start_date,
        end_date,
        start_time=start_time,
        end_time=end_time,
        exclude_playlist_files=[Path(value) for value in args.exclude_playlist_files],
    )
    summary = cast(dict[str, object], payload["summary"])
    ordered_candidates = cast(list[dict[str, object]], payload["ordered_candidates"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        json.dumps(
            {
                "output_path": str(output_path).replace("/", "\\"),
                "input_rows": summary["input_recenttracks_rows"],
                "filtered_rows": summary["filtered_recenttracks_rows"],
                "excluded_rows": summary["excluded_recenttracks_rows"],
                "unique_pairs": summary["unique_artist_track_pairs"],
                "first_candidate": ordered_candidates[0] if ordered_candidates else None,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()