from __future__ import annotations

from html import escape
from pathlib import Path
import re

import httpx
from markdown import markdown as render_markdown

from .models import DiffReport


class WebhookError(RuntimeError):
    pass


def send_summary_webhook(
    *,
    webhook_url: str,
    report: DiffReport,
    markdown: str,
    snapshot_path: Path,
    diff_path: Path,
    summary_path: Path,
    timeout_seconds: float,
) -> None:
    html_body = render_markdown(markdown, extensions=["tables", "sane_lists"])
    html = _build_email_html(report, html_body)
    payload = {
        "playlist_id": report.playlist_id,
        "playlist_name": report.playlist_name,
        "generated_at": report.generated_at,
        "current_snapshot_at": report.current_snapshot_at,
        "previous_snapshot_at": report.previous_snapshot_at,
        "market": report.market,
        "is_initial_run": report.is_initial_run,
        "change_counts": report.to_dict()["summary"],
        "files": {
            "snapshot": {"name": snapshot_path.name, "path": str(snapshot_path)},
            "diff": {"name": diff_path.name, "path": str(diff_path)},
            "summary": {"name": summary_path.name, "path": str(summary_path)},
        },
        "markdown": markdown,
        "html": html,
    }

    try:
        response = httpx.post(webhook_url, json=payload, timeout=timeout_seconds)
    except httpx.HTTPError as error:
        raise WebhookError(f"Summary webhook request failed: {error}") from error

    if response.status_code >= 400:
        raise WebhookError(f"Summary webhook returned HTTP {response.status_code}: {response.text}")


def _build_email_html(report: DiffReport, html_body: str) -> str:
    html_body = _enhance_email_html(_remove_duplicate_title(html_body))
    title = escape(f"Playlist Summary: {report.playlist_name}")
    subtitle = escape(f"Playlist ID: {report.playlist_id}")
    return f"""<!DOCTYPE html>
<html lang=\"en\">
    <head>
        <meta charset=\"utf-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
        <meta name=\"color-scheme\" content=\"light\">
        <meta name=\"supported-color-schemes\" content=\"light\">
        <title>{title}</title>
    </head>
    <body bgcolor=\"#ffffff\" style=\"margin:0;padding:24px;background-color:#ffffff;color:#111827;font-family:Arial,Helvetica,sans-serif;\">
        <div style=\"width:100%;margin:0;\">
            <div style=\"padding:8px 0 22px;\">
                <div style=\"font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:#475467;margin-bottom:10px;\">Spotify Playlist Tracker</div>
                <h1 style=\"margin:0;font-size:34px;line-height:1.15;font-weight:700;color:#101828;\">{title}</h1>
                <p style=\"margin:8px 0 0;font-size:14px;line-height:1.5;color:#475467;\">{subtitle}</p>
            </div>
            <div>{html_body}</div>
        </div>
    </body>
</html>
"""


def _remove_duplicate_title(html_body: str) -> str:
    return re.sub(r"^\s*<h1>.*?</h1>\s*", "", html_body, count=1, flags=re.DOTALL)


def _enhance_email_html(html_body: str) -> str:
    section_styles = {
        "Added": "margin:24px 0 12px;font-size:22px;line-height:1.3;font-family:Arial,Helvetica,sans-serif;color:#0f766e;padding-bottom:6px;border-bottom:1px solid #5eead4;",
        "Removed": "margin:24px 0 12px;font-size:22px;line-height:1.3;font-family:Arial,Helvetica,sans-serif;color:#dc2626;padding-bottom:6px;border-bottom:1px solid #fca5a5;",
        "Reordered": "margin:24px 0 12px;font-size:22px;line-height:1.3;font-family:Arial,Helvetica,sans-serif;color:#7c3aed;padding-bottom:6px;border-bottom:1px solid #c4b5fd;",
        "Changed": "margin:24px 0 12px;font-size:22px;line-height:1.3;font-family:Arial,Helvetica,sans-serif;color:#b45309;padding-bottom:6px;border-bottom:1px solid #fdba74;",
        "Newly Unavailable": "margin:24px 0 12px;font-size:22px;line-height:1.3;font-family:Arial,Helvetica,sans-serif;color:#1d4ed8;padding-bottom:6px;border-bottom:1px solid #93c5fd;",
    }

    html_body = re.sub(
        r"<h2>(.*?)</h2>",
        lambda match: _style_h2(match.group(1), section_styles),
        html_body,
    )
    html_body = html_body.replace(
        "<ul>",
        '<ul style="margin:0 0 18px;padding-left:22px;color:#475467;">',
        1,
    )
    html_body = html_body.replace("<p>", '<p style="margin:0 0 14px;font-size:15px;line-height:1.65;color:#1f2937;">')
    html_body = html_body.replace("<li>", '<li style="margin:0 0 4px;font-size:15px;line-height:1.65;color:#1f2937;">')
    html_body = re.sub(
        r"<code>(.*?)</code>",
        lambda match: '<code style="background:#eef2ff;color:#1d4ed8;padding:2px 7px;border-radius:4px;font-size:13px;font-family:Consolas,\'Courier New\',monospace;border:1px solid #c7d2fe;">'
        + match.group(1)
        + "</code>",
        html_body,
        flags=re.DOTALL,
    )
    html_body = re.sub(r"<blockquote>", '<blockquote style="margin:16px 0;padding:12px 16px;background:#f8fafc;border-left:4px solid #98a2b3;color:#475467;">', html_body)
    html_body = re.sub(r"<hr ?/?>", '<hr style="border:none;border-top:1px solid #d8dde6;margin:24px 0;">', html_body)
    html_body = _style_tables(html_body)
    return html_body


def _style_h2(title: str, section_styles: dict[str, str]) -> str:
    style = section_styles.get(
        title,
        "margin:24px 0 12px;font-size:22px;line-height:1.3;font-family:Arial,Helvetica,sans-serif;color:#101828;padding-bottom:6px;border-bottom:1px solid #cfd4dc;",
    )
    return f'<h2 style="{style}">{title}</h2>'


def _style_tables(html_body: str) -> str:
    def replace_table(match: re.Match[str]) -> str:
        table_html = match.group(1)
        headers = [
            header.strip().lower()
            for header in re.findall(r"<th[^>]*>(.*?)</th>", table_html, flags=re.DOTALL)
        ]
        table_kind = _table_kind(headers)
        table_html = re.sub(
            r"<thead>(.*?)</thead>",
            lambda header_match: _style_thead(header_match, table_kind),
            table_html,
            flags=re.DOTALL,
        )

        row_index = 0

        def replace_row(row_match: re.Match[str]) -> str:
            nonlocal row_index
            row_html = row_match.group(1)
            background = "#ffffff" if row_index % 2 == 0 else "#f8fafc"
            row_index += 1
            cells: list[tuple[str, str]] = re.findall(r"<td([^>]*)>(.*?)</td>", row_html, flags=re.DOTALL)
            styled_cells = []
            for index, (attrs, content) in enumerate(cells):
                is_last = index == len(cells) - 1
                cell_style = _cell_style(
                    attrs=attrs,
                    base_style=(
                        "padding:10px 12px;border-right:1px solid #d8dde6;border-bottom:1px solid #d8dde6;"
                        f"vertical-align:top;background:{background};color:#111827;"
                    ),
                    is_last=is_last,
                )
                cell_style += _column_style(table_kind=table_kind, headers=headers, index=index, content=content)
                if content.strip().isdigit() and table_kind == "diff_summary":
                    cell_style += "white-space:nowrap;width:96px;"
                styled_cells.append(f'<td style="{cell_style}">{content}</td>')
            row_html = "".join(styled_cells)
            return f"<tr>{row_html}</tr>"

        def replace_tbody(tbody_match: re.Match[str]) -> str:
            tbody_html = re.sub(r"<tr>(.*?)</tr>", replace_row, tbody_match.group(1), flags=re.DOTALL)
            return f"<tbody>{tbody_html}</tbody>"

        table_html = re.sub(r"<tbody>(.*?)</tbody>", replace_tbody, table_html, flags=re.DOTALL)
        wrapper_style = 'width:100%;margin:16px 0 24px;border:1px solid #cfd4dc;border-radius:4px;overflow:hidden;'
        table_style = 'width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;margin:0;font-size:14px;background:#ffffff;'
        if table_kind == "diff_summary":
            wrapper_style = 'width:520px;max-width:100%;margin:16px 0 24px;border:1px solid #cfd4dc;border-radius:4px;overflow:hidden;'
            table_style = 'width:520px;max-width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;margin:0;font-size:14px;background:#ffffff;'
        return (
            f'<div style="{wrapper_style}">'
            f'<table cellpadding="0" cellspacing="0" style="{table_style}">'
            + table_html
            + "</table></div>"
        )

    return re.sub(r"<table>(.*?)</table>", replace_table, html_body, flags=re.DOTALL)


def _style_thead(thead_match: re.Match[str], table_kind: str) -> str:
    header_html = thead_match.group(1)
    headers = re.findall(r"<th[^>]*>(.*?)</th>", header_html, flags=re.DOTALL)
    normalized_headers = [header.strip().lower() for header in headers]

    def replace_header_cell(cell_match: re.Match[str]) -> str:
        content = cell_match.group(2)
        is_count = content.strip().lower() == "count"
        cell_style = _cell_style(
            attrs=cell_match.group(1),
            base_style=(
                "background:#ffffff;color:#344054;padding:10px 12px;border-right:1px solid #cfd4dc;"
                "border-bottom:1px solid #cfd4dc;font-weight:700;"
            ),
            is_last=content == headers[-1],
        )
        cell_style += _column_style(
            table_kind=table_kind,
            headers=normalized_headers,
            index=headers.index(content),
            content=content,
        )
        if is_count and table_kind == "diff_summary":
            cell_style += "white-space:nowrap;width:96px;"
        return f'<th style="{cell_style}">{content}</th>'

    header_html = re.sub(r"<th([^>]*)>(.*?)</th>", replace_header_cell, header_html, flags=re.DOTALL)
    return f"<thead>{header_html}</thead>"


def _table_kind(headers: list[str]) -> str:
    normalized_headers = [_normalize_header_label(header) for header in headers]
    if normalized_headers == ["change type", "count"]:
        return "diff_summary"
    if normalized_headers == ["song", "artists", "reason", "explanation"]:
        return "unavailable_songs"
    return "default"


def _column_style(*, table_kind: str, headers: list[str], index: int, content: str) -> str:
    normalized_headers = [_normalize_header_label(header) for header in headers]
    if table_kind == "diff_summary":
        if index == 0:
            return "width:376px;"
        if index == 1:
            return "width:96px;white-space:nowrap;"
    if table_kind == "unavailable_songs":
        if index == 0:
            return "width:23%;"
        if index == 1:
            return "width:23%;"
        if index == 2:
            return "width:10%;white-space:nowrap;"
        if index == 3:
            return "width:44%;"
    if index < len(normalized_headers) and normalized_headers[index] == "explanation":
        return "width:44%;"
    if content.strip().isdigit():
        return "white-space:nowrap;"
    return ""


def _normalize_header_label(header: str) -> str:
    return re.sub(r"<.*?>", "", header).strip().lower()


def _cell_style(*, attrs: str, base_style: str, is_last: bool) -> str:
    if 'text-align: right' in attrs or 'align="right"' in attrs or "align='right'" in attrs:
        alignment_style = "text-align:right;"
    elif 'text-align: center' in attrs or 'align="center"' in attrs or "align='center'" in attrs:
        alignment_style = "text-align:center;"
    else:
        alignment_style = "text-align:left;"

    if is_last:
        base_style = base_style.replace("border-right:1px solid #d8dde6;", "border-right:none;")
        base_style = base_style.replace("border-right:1px solid #cfd4dc;", "border-right:none;")

    return alignment_style + base_style