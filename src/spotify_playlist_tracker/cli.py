from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .auth import AuthError, TokenStore, authorize, get_valid_token
from .diff import compare_snapshots
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
            return run_check(settings, force_summary=args.force_summary)

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


def run_check(settings: AppSettings, force_summary: bool = False) -> int:
    token_store = TokenStore(settings.paths.token_file)
    token = get_valid_token(settings, token_store)
    snapshot_store = SnapshotStore(settings.paths.results_dir)

    had_errors = False
    with SpotifyClient(settings, token.access_token) as client:
        for playlist_id in settings.playlists.playlist_ids:
            try:
                previous = snapshot_store.load_latest_snapshot(playlist_id)
                current = client.fetch_playlist_snapshot(playlist_id)
                report = compare_snapshots(previous, current)
                snapshot_path = snapshot_store.save_snapshot(current)
                diff_path = snapshot_store.save_diff(report)
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

                print(report.format_console())
                print(f"  Snapshot: {snapshot_path}")
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
    if not settings.runtime.schedule:
        raise SettingsError("TRACKER_SCHEDULE is required for the run command.")

    latest_exit_code = run_check(settings)
    while True:
        now = time.time()
        next_run = next_run_after(settings.runtime.schedule)
        sleep_seconds = max(next_run.timestamp() - now, 0)
        print(f"Next scheduled run at {next_run.isoformat().replace('+00:00', 'Z')}")
        time.sleep(sleep_seconds)
        latest_exit_code = run_check(settings)
