from __future__ import annotations

import html
import re

CHUNK_LIMIT = 3800


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def to_telegram_html(text: str) -> str:
    """Markdown javobni Telegram qo'llab-quvvatlaydigan HTML'ga aylantiradi."""
    stash: list[str] = []

    def keep(rendered: str) -> str:
        stash.append(rendered)
        return f"\x00{len(stash) - 1}\x00"

    text = re.sub(
        r"```[^\n`]*\n?(.*?)```",
        lambda m: keep(f"<pre><code>{escape_html(m.group(1))}</code></pre>"),
        text,
        flags=re.S,
    )
    text = re.sub(
        r"`([^`\n]+)`",
        lambda m: keep(f"<code>{escape_html(m.group(1))}</code>"),
        text,
    )

    text = escape_html(text)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        if not re.match(r"^(https?://|tg://)", url):
            return match.group(0)
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"^\s*#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.S)
    text = re.sub(r"^(\s*)[-*]\s+", r"\1• ", text, flags=re.M)

    for index, rendered in enumerate(stash):
        text = text.replace(f"\x00{index}\x00", rendered)
    return text


def split_message(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        for piece in _fit(block, limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _fit(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]

    pieces: list[str] = []
    current = ""
    for line in block.split("\n"):
        while len(line) > limit:
            pieces.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = line
    if current:
        pieces.append(current)
    return pieces
