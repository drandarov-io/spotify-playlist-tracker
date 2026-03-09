from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class SettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpotifyCredentials:
    client_id: str | None
    client_secret: str | None
    redirect_uri: str

    def validate(self) -> None:
        if not self.client_id or not self.client_secret:
            raise SettingsError(
                "Spotify credentials are missing. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in the environment or .env file."
            )


@dataclass(frozen=True)
class PlaylistConfig:
    playlist_ids: tuple[str, ...]
    market: str
    include_episodes: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    schedule: str
    summary_webhook_url: str | None
    webhook_timeout_seconds: float
    auth_bind_host: str | None


@dataclass(frozen=True)
class PathConfig:
    root_dir: Path
    results_dir: Path
    token_file: Path


@dataclass(frozen=True)
class AppSettings:
    spotify: SpotifyCredentials
    playlists: PlaylistConfig
    runtime: RuntimeConfig
    paths: PathConfig

    @classmethod
    def load(cls, root_dir: Path, require_playlists: bool = True) -> "AppSettings":
        root_dir = root_dir.resolve()
        load_dotenv(root_dir / ".env")

        spotify = SpotifyCredentials(
            client_id=_empty_to_none(os.getenv("SPOTIFY_CLIENT_ID")),
            client_secret=_empty_to_none(os.getenv("SPOTIFY_CLIENT_SECRET")),
            redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8899/callback").strip(),
        )

        playlist_ids = _parse_csv_env("SPOTIFY_PLAYLIST_IDS") if require_playlists else tuple(
            item.strip() for item in os.getenv("SPOTIFY_PLAYLIST_IDS", "").split(",") if item.strip()
        )
        market = os.getenv("SPOTIFY_MARKET", "US").strip().upper()
        if len(market) != 2:
            raise SettingsError("SPOTIFY_MARKET must be a two-letter country code.")

        playlists = PlaylistConfig(
            playlist_ids=playlist_ids,
            market=market,
            include_episodes=_parse_bool(os.getenv("SPOTIFY_INCLUDE_EPISODES"), False),
        )

        runtime = RuntimeConfig(
            schedule=os.getenv("TRACKER_SCHEDULE", "daily").strip() or "daily",
            summary_webhook_url=_validate_optional_url("TRACKER_SUMMARY_WEBHOOK_URL"),
            webhook_timeout_seconds=_parse_positive_float("TRACKER_WEBHOOK_TIMEOUT_SECONDS", 15.0),
            auth_bind_host=_empty_to_none(os.getenv("TRACKER_AUTH_BIND_HOST")),
        )

        paths = PathConfig(
            root_dir=root_dir,
            results_dir=_resolve_path(root_dir, os.getenv("TRACKER_RESULTS_DIR"), Path("results")),
            token_file=_resolve_path(root_dir, os.getenv("TRACKER_AUTH_FILE"), Path("state") / ".auth"),
        )

        return cls(spotify=spotify, playlists=playlists, runtime=runtime, paths=paths)


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _resolve_path(root_dir: Path, configured: str | None, default: Path) -> Path:
    candidate = Path(configured.strip()) if configured and configured.strip() else default
    if candidate.is_absolute():
        return candidate.resolve()
    return (root_dir / candidate).resolve()


def _parse_csv_env(name: str) -> tuple[str, ...]:
    raw_value = _empty_to_none(os.getenv(name))
    if raw_value is None:
        raise SettingsError(f"{name} is required and must contain at least one comma-separated playlist ID.")
    values = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    if not values:
        raise SettingsError(f"{name} is required and must contain at least one comma-separated playlist ID.")
    return values


def _parse_bool(raw_value: str | None, default: bool) -> bool:
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"Invalid boolean value: {raw_value}")


def _parse_positive_float(name: str, default: float) -> float:
    raw_value = _empty_to_none(os.getenv(name))
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as error:
        raise SettingsError(f"{name} must be a positive number.") from error
    if value <= 0:
        raise SettingsError(f"{name} must be a positive number.")
    return value


def _validate_optional_url(name: str) -> str | None:
    raw_value = _empty_to_none(os.getenv(name))
    if raw_value is None:
        return None
    if not raw_value.startswith(("http://", "https://")):
        raise SettingsError(f"{name} must start with http:// or https://")
    return raw_value
