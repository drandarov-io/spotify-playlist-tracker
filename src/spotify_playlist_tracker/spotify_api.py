from __future__ import annotations

import time
from typing import Any

import httpx

from .models import PlaylistEntry, PlaylistSnapshot, isoformat_now
from .settings import AppSettings


class SpotifyApiError(RuntimeError):
    pass


class SpotifyClient:
    def __init__(self, settings: AppSettings, access_token: str) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url="https://api.spotify.com/v1",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SpotifyClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def fetch_playlist_snapshot(self, playlist_id: str) -> PlaylistSnapshot:
        metadata = self._request(
            "GET",
            f"/playlists/{playlist_id}",
            params={
                "fields": "id,name,snapshot_id,tracks.total",
                "market": self._settings.playlists.market,
            },
        )
        items = self._fetch_playlist_items(playlist_id)
        return PlaylistSnapshot(
            playlist_id=playlist_id,
            playlist_name=str(metadata.get("name", playlist_id)),
            fetched_at=isoformat_now(),
            market=self._settings.playlists.market,
            total_items=int(metadata.get("tracks", {}).get("total", len(items))),
            snapshot_id=metadata.get("snapshot_id"),
            entries=tuple(items),
        )

    def _fetch_playlist_items(self, playlist_id: str) -> list[PlaylistEntry]:
        items: list[PlaylistEntry] = []
        offset = 0

        while True:
            params: dict[str, Any] = {
                "market": self._settings.playlists.market,
                "limit": 50,
                "offset": offset,
            }
            if self._settings.playlists.include_episodes:
                params["additional_types"] = "track,episode"

            payload = self._request("GET", f"/playlists/{playlist_id}/items", params=params)
            raw_items = payload.get("items", [])
            for raw_item in raw_items:
                normalized = self._normalize_item(raw_item, len(items))
                if normalized is not None:
                    items.append(normalized)

            next_link = payload.get("next")
            if not next_link:
                return items
            offset += len(raw_items)

    def _normalize_item(self, raw_item: dict[str, Any], position: int) -> PlaylistEntry | None:
        item = raw_item.get("item")
        if item is None and raw_item.get("track") is not None:
            item = raw_item.get("track")

        item_type = str((item or {}).get("type", "track"))
        if item_type == "episode" and not self._settings.playlists.include_episodes:
            return None

        artists = tuple(artist.get("name", "") for artist in (item or {}).get("artists", []) if artist.get("name"))
        restrictions = (item or {}).get("restrictions") or {}
        linked_from = (item or {}).get("linked_from") or {}
        album = (item or {}).get("album") or {}
        added_by = raw_item.get("added_by") or {}

        return PlaylistEntry(
            position=position,
            item_type=item_type,
            spotify_id=(item or {}).get("id"),
            uri=(item or {}).get("uri"),
            name=(item or {}).get("name"),
            artists=artists,
            album=album.get("name"),
            duration_ms=(item or {}).get("duration_ms"),
            explicit=(item or {}).get("explicit"),
            is_local=bool(raw_item.get("is_local") or (item or {}).get("is_local", False)),
            added_at=raw_item.get("added_at"),
            added_by=added_by.get("id"),
            is_playable=(item or {}).get("is_playable"),
            restriction_reason=restrictions.get("reason"),
            linked_from_id=linked_from.get("id"),
        )

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        attempts = 0
        while True:
            response = self._client.request(method, path, params=params)
            if response.status_code == 429 and attempts < 3:
                retry_after = int(response.headers.get("Retry-After", "1"))
                time.sleep(retry_after)
                attempts += 1
                continue
            if response.status_code == 401:
                raise SpotifyApiError("Spotify request failed with 401 Unauthorized. Re-run authorization.")
            if response.status_code == 403:
                raise SpotifyApiError(
                    "Spotify request failed with 403 Forbidden. Confirm the authorized Spotify user owns or collaborates on the playlist."
                )
            if response.status_code == 404:
                raise SpotifyApiError(f"Spotify playlist not found: {path}")

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise SpotifyApiError(f"Spotify API error: {error.response.status_code} {error.response.text}") from error
            return response.json()
