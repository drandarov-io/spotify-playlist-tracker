from pathlib import Path

import pytest

from spotify_playlist_tracker.models import DiffReport
from spotify_playlist_tracker.webhook import WebhookError, send_summary_webhook


class DummyResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_send_summary_webhook_posts_markdown_and_metadata(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse(200)

    monkeypatch.setattr("spotify_playlist_tracker.webhook.httpx.post", fake_post)

    report = DiffReport(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        generated_at="2026-03-09T10:00:00Z",
        current_snapshot_at="2026-03-09T10:00:00Z",
        previous_snapshot_at=None,
        market="DE",
        is_initial_run=True,
    )

    send_summary_webhook(
        webhook_url="https://example.test/hook",
        report=report,
        markdown=(
            "# Summary\n\n"
            "Market: `DE`\n\n"
            "| Change Type | Count |\n"
            "| --- | ---: |\n"
            "| Added | 0 |\n\n"
            "| Song | Artists | Reason | Explanation |\n"
            "| --- | --- | --- | --- |\n"
            "| Example | Artist | market | The track is not playable in the configured market `DE`. |\n"
        ),
        snapshot_path=tmp_path / "snapshot.json",
        diff_path=tmp_path / "diff.json",
        summary_path=tmp_path / "summary.md",
        timeout_seconds=10.0,
    )

    assert captured["url"] == "https://example.test/hook"
    assert "# Summary" in captured["json"]["markdown"]
    assert captured["json"]["html"].startswith("<!DOCTYPE html>")
    assert "Spotify Playlist Tracker" in captured["json"]["html"]
    assert "Summary" in captured["json"]["html"]
    assert "<style>" not in captured["json"]["html"]
    assert 'meta name="color-scheme" content="light"' in captured["json"]["html"]
    assert "border-radius:4px" in captured["json"]["html"]
    assert "background-color:#ffffff" in captured["json"]["html"]
    assert "<code style=" in captured["json"]["html"]
    assert "<table cellpadding=\"0\" cellspacing=\"0\" style=" in captured["json"]["html"]
    assert "text-align:right;" in captured["json"]["html"]
    assert "width:96px;" in captured["json"]["html"]
    assert "width:520px;max-width:100%;" in captured["json"]["html"]
    assert "width:44%;" in captured["json"]["html"]
    assert captured["json"]["html"].count("Playlist Summary: Playlist") == 2
    assert captured["json"]["html"].count("<h1 style=") == 1
    assert captured["json"]["change_counts"]["added"] == 0
    assert captured["json"]["files"]["summary"]["name"] == "summary.md"
    assert captured["timeout"] == 10.0


def test_send_summary_webhook_raises_for_failure(monkeypatch, tmp_path) -> None:
    def fake_post(url, json, timeout):
        return DummyResponse(500, "server error")

    monkeypatch.setattr("spotify_playlist_tracker.webhook.httpx.post", fake_post)

    report = DiffReport(
        playlist_id="playlist-1",
        playlist_name="Playlist",
        generated_at="2026-03-09T10:00:00Z",
        current_snapshot_at="2026-03-09T10:00:00Z",
        previous_snapshot_at="2026-03-08T10:00:00Z",
        market="DE",
        is_initial_run=False,
    )

    with pytest.raises(WebhookError):
        send_summary_webhook(
            webhook_url="https://example.test/hook",
            report=report,
            markdown="# Summary",
            snapshot_path=tmp_path / "snapshot.json",
            diff_path=tmp_path / "diff.json",
            summary_path=tmp_path / "summary.md",
            timeout_seconds=10.0,
        )