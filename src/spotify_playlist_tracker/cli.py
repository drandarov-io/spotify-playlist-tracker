from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .auth import AuthError, TokenStore, authorize, get_valid_token
from .diff import compare_snapshots
from .models import PlaylistEntry
from .settings import AppSettings, SettingsError
from .scheduler import ScheduleError, next_run_after
from .spotify_api import SpotifyApiError, SpotifyClient
from .storage import SnapshotStore
from .webhook import WebhookError, send_summary_webhook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spotify-playlist-tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("authorize", help="Authorize the app against a Spotify user account.")
    check_parser = subparsers.add_parser("check", help="Fetch playlists, diff against the last run, and persist results.")
    check_parser.add_argument(
        "--force-summary",
        action="store_true",
        help="Write a markdown summary even when the diff would normally skip summary generation.",
    )
    check_parser.add_argument(
        "--raw-output",
        action="store_true",
        help="Print raw JSON output with full diff fields and reasons instead of the human-readable summary.",
    )
    subparsers.add_parser(
        "checkunavailable",
        help="Fetch current playlists, enrich unavailable tracks with market metadata, and persist unavailable summary JSON only.",
    )
    subparsers.add_parser("run", help="Run an immediate check, then repeat according to TRACKER_SCHEDULE.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = AppSettings.load(Path.cwd(), require_playlists=args.command != "authorize")

        if args.command == "authorize":
            token_store = TokenStore(settings.paths.token_file)
            token = authorize(settings)
            token_store.save(token)
            print(f"Saved Spotify token to {settings.paths.token_file}")
            print(settings.paths.token_file.read_text(encoding="utf-8"))
            return 0

        if args.command == "check":
            return run_check(settings, force_summary=args.force_summary, raw_output=args.raw_output)

        if args.command == "checkunavailable":
            return run_check_unavailable(settings)

        if args.command == "run":
            return run_scheduled(settings)

        parser.error(f"Unsupported command: {args.command}")
    except (AuthError, SettingsError, ScheduleError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    return 0


def run_check(settings: AppSettings, force_summary: bool = False, raw_output: bool = False) -> int:
    token_store = TokenStore(settings.paths.token_file)
    token = get_valid_token(settings, token_store)
    snapshot_store = SnapshotStore(settings.paths.results_dir)

    had_errors = False
    with SpotifyClient(settings, token.access_token) as client:
        for playlist_id in settings.playlists.playlist_ids:
            try:
                previous = snapshot_store.load_latest_snapshot(playlist_id)
                fetched = client.fetch_playlist_data(playlist_id)
                current = fetched.snapshot
                report = compare_snapshots(previous, current)
                snapshot_path = snapshot_store.save_snapshot(current)
                raw_path = snapshot_store.save_raw(current.fetched_at, current.playlist_name, current.playlist_id, fetched.raw_payload)
                diff_path = snapshot_store.save_diff(report) if report.has_changes else None
                summary_path = None

                if report.should_create_summary_for(force_summary):
                    summary_path = snapshot_store.save_summary(report, current)
                    if settings.runtime.summary_webhook_url:
                        markdown = summary_path.read_text(encoding="utf-8")
                        send_summary_webhook(
                            webhook_url=settings.runtime.summary_webhook_url,
                            report=report,
                            markdown=markdown,
                            snapshot_path=snapshot_path,
                            diff_path=diff_path,
                            summary_path=summary_path,
                            timeout_seconds=settings.runtime.webhook_timeout_seconds,
                        )

                if raw_output:
                    print(
                        json.dumps(
                            _build_raw_check_output(
                                report=report,
                                snapshot_path=snapshot_path,
                                raw_path=raw_path,
                                diff_path=diff_path,
                                summary_path=summary_path,
                            ),
                            indent=2,
                        )
                    )
                else:
                    print(f"  Snapshot: {snapshot_path}")
                    print(f"  Raw: {raw_path}")
                    if diff_path is not None:
                        print(f"  Diff: {diff_path}")
                    if summary_path is not None:
                        print(f"  Summary: {summary_path}")
                        if settings.runtime.summary_webhook_url:
                            print(f"  Webhook: delivered to {settings.runtime.summary_webhook_url}")
                    print()
            except (SpotifyApiError, WebhookError) as error:
                had_errors = True
                print(f"Playlist {playlist_id}: {error}", file=sys.stderr)

    return 1 if had_errors else 0


def run_scheduled(settings: AppSettings) -> int:
    latest_exit_code = run_check(settings)
    while True:
        now = time.time()
        next_run = next_run_after(settings.runtime.schedule)
        sleep_seconds = max(next_run.timestamp() - now, 0)
        print(f"Next scheduled run at {next_run.isoformat().replace('+00:00', 'Z')}")
        time.sleep(sleep_seconds)
        latest_exit_code = run_check(settings)


def run_check_unavailable(settings: AppSettings) -> int:
    token_store = TokenStore(settings.paths.token_file)
    token = get_valid_token(settings, token_store)
    snapshot_store = SnapshotStore(settings.paths.results_dir)

    had_errors = False
    with SpotifyClient(settings, token.access_token) as client:
        for playlist_id in settings.playlists.playlist_ids:
            try:
                current = client.fetch_playlist_snapshot(playlist_id)
                unavailable_entries = [entry for entry in current.entries if not entry.is_available]
                lookup_ids = [entry.spotify_id for entry in unavailable_entries if entry.spotify_id and entry.item_type == "track"]
                track_metadata = client.fetch_tracks_metadata(lookup_ids)
                payload = _build_unavailable_summary_output(current, unavailable_entries, track_metadata)
                unavailable_summary_path = snapshot_store.save_unavailable_summary(
                    current.fetched_at,
                    current.playlist_name,
                    current.playlist_id,
                    payload,
                )
                unavailable_summary_markdown_path = snapshot_store.save_unavailable_summary_markdown(
                    current.fetched_at,
                    current.playlist_name,
                    current.playlist_id,
                    _format_unavailable_summary_markdown(payload),
                )
                print(f"  Unavailable summary: {unavailable_summary_path}")
                print(f"  Unavailable summary markdown: {unavailable_summary_markdown_path}")
            except SpotifyApiError as error:
                had_errors = True
                print(f"Playlist {playlist_id}: {error}", file=sys.stderr)

    return 1 if had_errors else 0


def _build_raw_check_output(
    *,
    report,
    snapshot_path: Path,
    raw_path: Path,
    diff_path: Path | None,
    summary_path: Path | None,
) -> dict[str, object]:
    payload = report.to_dict()
    payload["files"] = {
        "snapshot": {"name": snapshot_path.name, "path": str(snapshot_path)},
        "raw": {"name": raw_path.name, "path": str(raw_path)},
        "diff": None if diff_path is None else {"name": diff_path.name, "path": str(diff_path)},
        "summary": None if summary_path is None else {"name": summary_path.name, "path": str(summary_path)},
    }
    return payload


def _build_unavailable_summary_output(
    snapshot,
    unavailable_entries: list[PlaylistEntry],
    track_metadata: dict[str, dict[str, object]],
) -> dict[str, object]:
    entries = [
        _build_unavailable_entry_payload(entry, snapshot.market, track_metadata.get(entry.spotify_id or ""))
        for entry in unavailable_entries
    ]
    return {
        "playlist_id": snapshot.playlist_id,
        "playlist_name": snapshot.playlist_name,
        "generated_at": snapshot.fetched_at,
        "market": snapshot.market,
        "unavailable_count": len(entries),
        "lookup_track_count": len(track_metadata),
        "items": entries,
    }


def _build_unavailable_entry_payload(
    entry: PlaylistEntry,
    market: str,
    lookup_track: dict[str, object] | None,
) -> dict[str, object]:
    lookup_available_markets = None if lookup_track is None else lookup_track.get("available_markets")
    available_markets = None
    if isinstance(lookup_available_markets, list):
        available_markets = [str(item) for item in lookup_available_markets]

    display_name = entry.name or (None if lookup_track is None else lookup_track.get("name")) or "Unknown"
    lookup_artists = []
    if lookup_track is not None:
        raw_artists = lookup_track.get("artists")
        if isinstance(raw_artists, list):
            lookup_artists = [
                str(artist.get("name"))
                for artist in raw_artists
                if isinstance(artist, dict) and artist.get("name")
            ]
    display_artists = list(entry.artists) or lookup_artists or ["Unknown"]
    configured_market_available = None if available_markets is None else market in available_markets

    return {
        "position": entry.position,
        "item_type": entry.item_type,
        "spotify_id": entry.spotify_id,
        "uri": entry.uri,
        "name": display_name,
        "artists": display_artists,
        "album": entry.album,
        "availability_status": entry.availability_status,
        "restriction_reason": entry.restriction_reason,
        "is_playable": entry.is_playable,
        "linked_from_id": entry.linked_from_id,
        "explanation": _build_unavailable_market_explanation(market, available_markets),
        "market_lookup": None
        if lookup_track is None
        else {
            "looked_up_track_id": lookup_track.get("id"),
            "available_markets": available_markets,
            "available_market_count": None if available_markets is None else len(available_markets),
            "configured_market_available": configured_market_available,
            "restrictions": lookup_track.get("restrictions"),
        },
    }


def _build_unavailable_market_explanation(market: str, available_markets: list[str] | None) -> str:
    if available_markets is None:
        return f"Spotify reported the track as unavailable in {market}, but did not return available_markets in the track lookup."
    if market in available_markets:
        return f"Spotify lists the track as available in {market} and {len(available_markets)} markets overall; the playlist item may be affected by relinking or account-specific playback context."
    return f"The track is not available in {market} and is available in {len(available_markets)} markets overall."


def _format_unavailable_summary_markdown(payload: dict[str, object]) -> str:
    lines = [
        f"# Unavailable Track Summary: {payload['playlist_name']}",
        "",
        f"- Playlist ID: `{payload['playlist_id']}`",
        f"- Market: `{payload['market']}`",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Unavailable count: `{payload['unavailable_count']}`",
        "",
        "| URI | Name | Artists | Album | Available markets | Explanation |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    items = payload.get("items", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            artists = item.get("artists")
            artist_text = ", ".join(str(artist) for artist in artists) if isinstance(artists, list) and artists else "Unknown"
            market_lookup = item.get("market_lookup")
            available_markets = None if not isinstance(market_lookup, dict) else market_lookup.get("available_markets")
            if isinstance(available_markets, list):
                available_market_text = ", ".join(str(market) for market in available_markets) if available_markets else "None"
            else:
                available_market_text = "Unknown"

            lines.append(
                "| {uri} | {name} | {artists} | {album} | {available_markets} | {explanation} |".format(
                    uri=_markdown_escape(str(item.get("uri") or "Unknown")),
                    name=_markdown_escape(str(item.get("name") or "Unknown")),
                    artists=_markdown_escape(artist_text),
                    album=_markdown_escape(str(item.get("album") or "Unknown")),
                    available_markets=_markdown_escape(available_market_text),
                    explanation=_markdown_escape(str(item.get("explanation") or "Unknown")),
                )
            )

    return "\n".join(lines) + "\n"


def _markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")
