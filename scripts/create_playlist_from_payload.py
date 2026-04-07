from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HTTP_TIMEOUT = 30.0
MAX_RETRIES = 5
MAX_BACKOFF = 10
RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
SEARCH_LIMIT = 5
BATCH_SIZE = 100
MIN_MATCH_SCORE = 70
TOKEN_EXPIRY_BUFFER = 60

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_token(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_token(path: Path, token_data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8")


def refresh_access_token(client_id: str, client_secret: str, token_data: dict[str, Any]) -> dict[str, Any]:
    response = httpx.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
        },
        auth=(client_id, client_secret),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    expires_in = int(payload["expires_in"])
    return {
        "access_token": str(payload["access_token"]),
        "refresh_token": str(payload.get("refresh_token") or token_data["refresh_token"]),
        "expires_at": time.time() + expires_in,
        "scope": str(payload.get("scope", token_data.get("scope", ""))),
        "token_type": str(payload.get("token_type", token_data.get("token_type", "Bearer"))),
    }


def ensure_access_token(client_id: str, client_secret: str, token_path: Path) -> dict[str, Any]:
    token_data = load_token(token_path)
    if float(token_data.get("expires_at", 0)) <= time.time() + TOKEN_EXPIRY_BUFFER:
        token_data = refresh_access_token(client_id, client_secret, token_data)
        save_token(token_path, token_data)
    return token_data


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def make_session(access_token: str) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.spotify.com/v1",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=HTTP_TIMEOUT,
    )


def request_json_with_retry(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempts = 0
    while True:
        try:
            response = client.request(method, path, params=params, json=json_body)
        except httpx.RequestError as error:
            if attempts < MAX_RETRIES:
                delay = min(2 ** attempts, MAX_BACKOFF)
                print(
                    f"[request-error] {method} {path} type={type(error).__name__} "
                    f"retrying in {delay}s (attempt {attempts + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)
                attempts += 1
                continue
            raise

        if response.status_code == 429 and attempts < MAX_RETRIES:
            wait_seconds = max(1, int(response.headers.get("Retry-After", "1")))
            print(
                f"[rate-limit] {method} {path} honoring retry-after={wait_seconds}s "
                f"(attempt {attempts + 1}/{MAX_RETRIES})"
            )
            time.sleep(wait_seconds)
            attempts += 1
            continue

        if response.status_code in RETRYABLE_STATUSES and attempts < MAX_RETRIES:
            delay = min(2 ** attempts, MAX_BACKOFF)
            print(
                f"[transient-http-error] {method} {path} status={response.status_code} "
                f"retrying in {delay}s (attempt {attempts + 1}/{MAX_RETRIES})"
            )
            time.sleep(delay)
            attempts += 1
            continue

        if response.status_code >= 400:
            print(f"[http-error] {method} {path} status={response.status_code}")
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()


# ---------------------------------------------------------------------------
# Spotify API wrappers
# ---------------------------------------------------------------------------

def get_current_user_id(client: httpx.Client) -> str:
    return str(request_json_with_retry(client, "GET", "/me")["id"])


def create_playlist(client: httpx.Client, user_id: str, name: str, description: str, public: bool) -> dict[str, Any]:
    return request_json_with_retry(
        client,
        "POST",
        f"/users/{user_id}/playlists",
        json_body={"name": name, "description": description, "public": public},
    )


def get_playlist(client: httpx.Client, playlist_id: str) -> dict[str, Any]:
    return request_json_with_retry(client, "GET", f"/playlists/{playlist_id}", params={"fields": "id,name,external_urls"})


# ---------------------------------------------------------------------------
# Text normalisation & query parsing
# ---------------------------------------------------------------------------

def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in no_marks.casefold() if ch.isalnum())


def extract_track_artist(query: str) -> tuple[str | None, str | None]:
    match = re.search(r'track:"([^"]+)"\s+artist:"([^"]+)"', query)
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2).strip()


def compact_track_title(title: str) -> str:
    # Strip (feat. ...), (ft. ...), (prod. ...) in parentheses
    without_paren = re.sub(r"\s*\((feat\.|ft\.|prod\.).*?\)", "", title, flags=re.IGNORECASE)
    # Strip bare feat./ft./prod. and everything after (including optional preceding ' - ')
    without_bare = re.sub(r"(\s+-\s+|\s+)(?:feat\.|ft\.|prod\.).+$", "", without_paren, flags=re.IGNORECASE)
    return re.sub(r"\s*\([^)]*\)", "", without_bare).strip()


def strip_dash_subtitle(title: str) -> str:
    """Remove ' - anything' suffix, e.g. 'Song Name - Radio Edit' → 'Song Name'."""
    return re.sub(r"\s+-\s+.+$", "", title).strip()


def build_fallback_queries(query: str) -> list[str]:
    track, artist = extract_track_artist(query)
    if not track:
        return [query]

    compact = compact_track_title(track)
    nodash = strip_dash_subtitle(compact)

    fallbacks = [
        query,
        f'track:"{compact}" artist:"{artist}"',
    ]

    fallbacks.append(f'track:"{compact}"' if compact != track else f'track:"{track}"')

    if nodash and nodash != compact:
        fallbacks.append(f'track:"{nodash}" artist:"{artist}"')
        fallbacks.append(f'track:"{nodash}"')

    if artist and artist.lower() not in {"other", "unknown"}:
        primary_artist = re.split(r",| and | & | feat\.| ft\.", artist, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if primary_artist and primary_artist != artist:
            fallbacks.append(f'track:"{compact}" artist:"{primary_artist}"')
            if nodash and nodash != compact:
                fallbacks.append(f'track:"{nodash}" artist:"{primary_artist}"')

    return list(dict.fromkeys(item for item in fallbacks if item.strip()))


# ---------------------------------------------------------------------------
# Search & matching
# ---------------------------------------------------------------------------

def pick_best_uri(
    candidate_track: str | None,
    candidate_artist: str | None,
    items: list[dict[str, Any]],
) -> tuple[str | None, int, dict[str, Any] | None]:
    """Return (uri, score, match_info) for the best matching item."""
    if not items:
        return None, 0, None

    normalized_track = normalize_text(candidate_track or "")
    normalized_artist = normalize_text(candidate_artist or "")

    best_uri: str | None = None
    best_score = -1
    best_info: dict[str, Any] | None = None

    for item in items:
        uri = item.get("uri")
        name = str(item.get("name", ""))
        artists = item.get("artists", [])
        joined_artists = " ".join(
            str(a.get("name", "")) for a in artists if isinstance(a, dict)
        )

        n_name = normalize_text(name)
        n_artists = normalize_text(joined_artists)

        score = 0
        if normalized_track and n_name == normalized_track:
            score += 70
        elif normalized_track and normalized_track in n_name:
            score += 40

        if normalized_artist and normalized_artist in n_artists:
            score += 30

        if not normalized_artist and normalized_track and n_name == normalized_track:
            score += 10

        if score > best_score and isinstance(uri, str) and uri:
            best_score = score
            best_uri = uri
            best_info = {"track_name": name, "artists": joined_artists, "uri": uri}

    if best_score >= MIN_MATCH_SCORE:
        return best_uri, best_score, best_info
    return None, best_score, best_info


def resolve_track_uri(
    client: httpx.Client, query: str, market: str | None,
) -> tuple[str | None, list[str], dict[str, Any] | None]:
    """Returns (matched_uri_or_None, fallback_queries_tried, match_details).

    When no match qualifies, match_details contains the highest-scoring
    candidate seen across all fallback queries (if any).
    """
    candidate_track, candidate_artist = extract_track_artist(query)
    fallbacks = build_fallback_queries(query)

    best_reject_score = -1
    best_reject_info: dict[str, Any] | None = None

    for fallback_query in fallbacks:
        params: dict[str, Any] = {"q": fallback_query, "type": "track", "limit": SEARCH_LIMIT}
        if market:
            params["market"] = market

        payload = request_json_with_retry(client, "GET", "/search", params=params)
        items = payload.get("tracks", {}).get("items", [])
        if not isinstance(items, list) or not items:
            continue

        # Re-extract from fallback so pick_best_uri compares against the compact form
        fb_track, fb_artist = extract_track_artist(fallback_query)
        uri, score, match_info = pick_best_uri(fb_track or candidate_track, fb_artist or candidate_artist, items)
        if uri:
            details = {
                "matched_query": fallback_query,
                "score": score,
                **(match_info or {}),
            }
            return uri, fallbacks, details

        if score > best_reject_score and match_info:
            best_reject_score = score
            best_reject_info = {"query": fallback_query, "score": score, **(match_info or {})}

    return None, fallbacks, best_reject_info


def add_tracks_in_batches(client: httpx.Client, playlist_id: str, uris: list[str]) -> int:
    added = 0
    for start in range(0, len(uris), BATCH_SIZE):
        batch = uris[start : start + BATCH_SIZE]
        request_json_with_retry(client, "POST", f"/playlists/{playlist_id}/tracks", json_body={"uris": batch})
        added += len(batch)
    return added


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update a Spotify playlist from candidate search queries.")
    parser.add_argument(
        "--payload", required=True,
        help="Path to payload JSON containing ordered_candidates[].",
    )
    parser.add_argument(
        "--unresolved-report", default="",
        help="Path to a prior import report JSON. If set, only unresolved queries are retried.",
    )
    parser.add_argument("--name", default="", help="Playlist name to create (ignored when --playlist-id is provided).")
    parser.add_argument(
        "--playlist-id", default="",
        help="Existing playlist id. If set, matched tracks are appended there.",
    )
    parser.add_argument("--description", default="", help="Playlist description (used when creating a new playlist).")
    parser.add_argument("--public", action="store_true", help="Create a public playlist (default is private).")
    parser.add_argument("--max-candidates", type=int, default=0, help="Max candidates to process; 0 means all.")
    parser.add_argument("--min-listen-count", type=int, default=0, help="Skip candidates with listen_count below this value.")
    parser.add_argument("--test-run", type=int, default=0, metavar="N", help="Process N candidates without modifying the playlist. Outputs a *_test_run_report.json.")
    parser.add_argument("--results-dir", default="results", help="Directory for the output report.")
    parser.add_argument("--log-interval", type=int, default=25, help="Print progress every N candidates.")
    parser.add_argument("--circuit-window", type=int, default=50, help="Rolling window size for circuit breaker.")
    parser.add_argument("--circuit-error-rate", type=float, default=0.6, help="Error rate threshold to open circuit.")
    parser.add_argument("--circuit-min-errors", type=int, default=20, help="Min errors in window before circuit can open.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Candidate loading
# ---------------------------------------------------------------------------

def load_candidates(payload_path: Path, unresolved_report_path: Path | None) -> list[dict[str, Any]]:
    if unresolved_report_path is not None:
        report = json.loads(unresolved_report_path.read_text(encoding="utf-8"))
        unresolved = report.get("unresolved", [])
        if not isinstance(unresolved, list):
            raise RuntimeError("unresolved report is invalid: 'unresolved' must be a list.")
        return [
            {"search_query": item["query"], "listen_count": item.get("listen_count", 1)}
            for item in unresolved
            if isinstance(item, dict) and isinstance(item.get("query"), str) and item["query"].strip()
        ]

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    ordered_candidates = payload.get("ordered_candidates", [])
    if not isinstance(ordered_candidates, list):
        raise RuntimeError("payload.ordered_candidates must be a list.")
    return ordered_candidates


# ---------------------------------------------------------------------------
# Core processing loop
# ---------------------------------------------------------------------------

def _log_progress(
    index: int, total: int, resolved_count: int, unresolved_count: int,
    last_status: str, started_at: float, log_interval: int,
) -> None:
    if log_interval > 0 and index % log_interval == 0:
        elapsed = int(time.time() - started_at)
        print(
            f"[{index}/{total}] resolved={resolved_count} "
            f"unresolved={unresolved_count} last={last_status} elapsed={elapsed}s"
        )


def process_candidates(
    client: httpx.Client,
    candidates: list[dict[str, Any]],
    market: str | None,
    *,
    log_interval: int,
    circuit_window: int,
    circuit_error_rate: float,
    circuit_min_errors: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str]:
    """Returns (resolved, unresolved, circuit_opened, circuit_reason)."""
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    window_size = max(circuit_window, 1)
    error_window: deque[bool] = deque(maxlen=window_size)
    circuit_opened = False
    circuit_reason = ""
    total = len(candidates)
    started_at = time.time()

    def _trip_circuit(index: int) -> bool:
        nonlocal circuit_opened, circuit_reason
        if len(error_window) < window_size:
            return False
        failures = sum(error_window)
        rate = failures / len(error_window)
        if failures >= circuit_min_errors and rate >= circuit_error_rate:
            circuit_opened = True
            circuit_reason = (
                f"high transient/search error rate: {failures}/{len(error_window)} ({rate:.0%}) "
                f"at candidate index {index}"
            )
            print(f"[circuit-breaker] OPEN: {circuit_reason}")
            return True
        return False

    for index, candidate in enumerate(candidates, start=1):
        last_status = ""

        if not isinstance(candidate, dict):
            unresolved.append({"index": index, "reason": "invalid-candidate"})
            last_status = "invalid-candidate"
            error_window.append(False)
            _log_progress(index, total, len(resolved), len(unresolved), last_status, started_at, log_interval)
            continue

        query = candidate.get("search_query")
        listen_count = candidate.get("listen_count", 1)
        if not isinstance(query, str) or not query.strip():
            unresolved.append({"index": index, "reason": "missing-search-query", "listen_count": listen_count, "candidate": candidate})
            last_status = "missing-search-query"
            error_window.append(False)
            _log_progress(index, total, len(resolved), len(unresolved), last_status, started_at, log_interval)
            continue

        is_transient = False
        try:
            uri, fallbacks_tried, match_details = resolve_track_uri(client, query, market)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            print(f"[search-http-error] index={index} status={code} query={query}")
            unresolved.append({"index": index, "reason": "search-http-error", "status_code": code, "query": query, "listen_count": listen_count})
            last_status = "search-http-error"
            is_transient = code in TRANSIENT_STATUSES
        except httpx.RequestError as exc:
            print(f"[search-request-error] index={index} type={type(exc).__name__} query={query}")
            unresolved.append({"index": index, "reason": "search-request-error", "query": query, "listen_count": listen_count})
            last_status = "search-request-error"
            is_transient = True
        else:
            if uri is None:
                no_match_entry: dict[str, Any] = {"index": index, "reason": "no-match", "query": query, "listen_count": listen_count, "fallback_queries": fallbacks_tried}
                if match_details:
                    no_match_entry["highest_score"] = match_details.get("score", 0)
                    no_match_entry["highest_scored_match"] = match_details
                unresolved.append(no_match_entry)
                last_status = "no-match"
            else:
                resolved.append({
                    "index": index,
                    "query": query,
                    "listen_count": listen_count,
                    "uri": uri,
                    **(match_details or {}),
                })
                last_status = "resolved"

        error_window.append(is_transient)
        if is_transient and _trip_circuit(index):
            break

        _log_progress(index, total, len(resolved), len(unresolved), last_status, started_at, log_interval)

    return resolved, unresolved, circuit_opened, circuit_reason


def build_report(
    args: argparse.Namespace,
    payload_path: Path,
    unresolved_report_path: Path | None,
    playlist_id: str,
    playlist_url: str,
    playlist_name: str,
    resolved: list[dict[str, Any]],
    added: int,
    unresolved: list[dict[str, Any]],
    processed_count: int,
    circuit_opened: bool,
    circuit_reason: str,
    *,
    test_run: bool = False,
) -> dict[str, Any]:
    return {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "test-run" if test_run else ("retry-unresolved" if unresolved_report_path is not None else "full-import"),
        "payload_path": str(payload_path),
        "unresolved_report_path": None if unresolved_report_path is None else str(unresolved_report_path),
        "playlist": {
            "id": playlist_id,
            "url": playlist_url,
            "name": playlist_name,
            "public": args.public,
        },
        "counts": {
            "candidates_processed": processed_count,
            "uris_resolved": len(resolved),
            "tracks_added": added,
            "unresolved": len(unresolved),
        },
        "circuit_breaker": {
            "opened": circuit_opened,
            "reason": circuit_reason,
            "window": args.circuit_window,
            "error_rate_threshold": args.circuit_error_rate,
            "min_errors": args.circuit_min_errors,
        },
        "resolved": resolved,
        "unresolved": unresolved,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    env = load_dotenv(Path(".env"))
    client_id = env.get("SPOTIFY_CLIENT_ID") or os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = env.get("SPOTIFY_CLIENT_SECRET") or os.getenv("SPOTIFY_CLIENT_SECRET")
    market = env.get("SPOTIFY_MARKET") or os.getenv("SPOTIFY_MARKET")

    if not client_id or not client_secret:
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are required.")

    token_data = ensure_access_token(client_id, client_secret, Path("state/.auth"))

    payload_path = Path(args.payload)
    unresolved_report_path = Path(args.unresolved_report) if args.unresolved_report else None
    candidates = load_candidates(payload_path, unresolved_report_path)

    if args.min_listen_count > 0:
        candidates = [c for c in candidates if c.get("listen_count", 1) >= args.min_listen_count]

    test_run = args.test_run > 0
    if test_run:
        candidates = candidates[: args.test_run]
    elif args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]

    with make_session(str(token_data["access_token"])) as client:
        playlist_id = ""
        playlist_url = ""
        playlist_name = ""

        if not test_run:
            playlist_id = args.playlist_id.strip()
            if playlist_id:
                playlist = get_playlist(client, playlist_id)
            else:
                if not args.name.strip():
                    raise RuntimeError("--name is required when creating a new playlist.")
                user_id = get_current_user_id(client)
                playlist = create_playlist(client, user_id, args.name, args.description, args.public)
                playlist_id = str(playlist["id"])

            playlist_url = str(playlist.get("external_urls", {}).get("spotify", ""))
            playlist_name = str(playlist.get("name", args.name or playlist_id))

        resolved, unresolved, circuit_opened, circuit_reason = process_candidates(
            client, candidates, market,
            log_interval=max(args.log_interval, 1),
            circuit_window=args.circuit_window,
            circuit_error_rate=args.circuit_error_rate,
            circuit_min_errors=args.circuit_min_errors,
        )

        # Deduplicate by URI while preserving order
        seen_uris: set[str] = set()
        unique_resolved: list[dict[str, Any]] = []
        for entry in resolved:
            if entry["uri"] not in seen_uris:
                seen_uris.add(entry["uri"])
                unique_resolved.append(entry)
        resolved = unique_resolved

        added = 0
        if not test_run and resolved:
            added = add_tracks_in_batches(client, playlist_id, [e["uri"] for e in resolved])

    report = build_report(
        args, payload_path, unresolved_report_path,
        playlist_id, playlist_url, playlist_name,
        resolved, added, unresolved, len(candidates),
        circuit_opened, circuit_reason,
        test_run=test_run,
    )

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_test_run_report.json" if test_run else "_playlist_import_report.json"
    report_path = results_dir / f"{time.strftime('%Y-%m-%dT%H-%M-%SZ', time.gmtime())}{suffix}"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    label = "Test run" if test_run else ("Retry" if unresolved_report_path else "Import")
    print(f"{label} complete.")
    print(f"Playlist: {playlist_name} ({playlist_id})")
    if playlist_url:
        print(f"URL: {playlist_url}")
    print(f"Candidates: {len(candidates)}  Resolved: {len(resolved)}  Added: {added}  Unresolved: {len(unresolved)}")
    print(f"Report: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
