"""Shared Rich building blocks for Paulo's terminal UI.

The palette is intentionally quiet: cool gray structure, muted blue accents,
amber for review states, and green/red only for final decisions.
"""

from __future__ import annotations

from rich.console import Group
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


PAULO_THEME = Theme(
    {
        "paulo.text": "#d4d4d4",
        "paulo.dim": "#737373",
        "paulo.faint": "#4b5563",
        "paulo.border": "#3f3f46",
        "paulo.accent": "#8fb4c7",
        "paulo.accent.bold": "bold #a7c7d8",
        "paulo.plan": "#c4a46b",
        "paulo.execute": "#8faa80",
        "paulo.success": "#87a887",
        "paulo.warn": "#c4a46b",
        "paulo.error": "#c98282",
        "paulo.bg": "on #202225",
        "paulo.bg.warn": "on #2b261d",
        "paulo.bg.ok": "on #1f281f",
        "paulo.bg.error": "on #2a1f1f",
        "paulo.bg.muted": "on #1d1f22",
        "paulo.tool": "#b8c4c9",
        "paulo.thinking": "#7f858a",
        "markdown": "#d4d4d4",
        "markdown.text": "#d4d4d4",
        "markdown.paragraph": "#d4d4d4",
        "markdown.h1": "bold #b8c4c9",
        "markdown.h1.border": "#3f4a50",
        "markdown.h2": "bold #aebdc3",
        "markdown.h2.border": "#3f4a50",
        "markdown.h3": "bold #a8b5bb",
        "markdown.h4": "bold #a1adb3",
        "markdown.h5": "bold #9aa6ab",
        "markdown.h6": "bold #939da3",
        "markdown.heading": "bold #aebdc3",
        "markdown.heading.border": "#3f4a50",
        "markdown.item": "#d4d4d4",
        "markdown.item.bullet": "#737373",
        "markdown.strong": "bold #d7d7d7",
        "markdown.em": "italic #c9c9c9",
        "markdown.code": "#c9d1d9 on #202225",
        "markdown.code_block": "#c9d1d9 on #1f2224",
        "markdown.block_quote": "#a8b5bb",
        "markdown.block_quote.border": "#4b5563",
        "markdown.hr": "#4b5563",
        "markdown.link": "underline #8fb4c7",
        "markdown.link_url": "#737373",
        "markdown.table": "#d4d4d4",
        "markdown.table.header": "bold #b8c4c9",
        "markdown.table.border": "#4b5563",
    }
)

MODE_STYLES = {
    "chat": ("CHAT", "paulo.accent"),
    "plan": ("PLAN", "paulo.plan"),
    "execute": ("EXEC", "paulo.execute"),
}

BODY_LEFT = 6


def trim(value: object, limit: int = 180) -> str:
    text = str(value or "").replace("\r", "").strip()
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def prefix(label: str, style: str = "paulo.accent", meta: str = "") -> Text:
    text = Text("\n  ")
    text.append("●", style=_dot_style(style))
    text.append(" ")
    text.append(f" {label.upper()} ", style=_badge_style(style))
    if meta:
        text.append("  ")
        text.append(meta, style="paulo.dim")
    return text


def status_line(label: str, message: str, style: str = "paulo.dim") -> Text:
    text = Text("\n  ")
    text.append("●", style=_dot_style(style))
    text.append(" ")
    text.append(f" {label.upper()} ", style=_badge_style(style))
    if message:
        text.append("  ")
        text.append(message, style="paulo.dim")
    return text


def tool_line(name: str, ok: bool = True, elapsed: str = "") -> Text:
    style = "paulo.success" if ok else "paulo.error"
    text = Text("\n  ")
    text.append("●", style=_dot_style(style))
    text.append(" ")
    text.append(f" {name.upper()} ", style=_tool_badge_style(name, ok))
    text.append("  ")
    text.append("DONE" if ok else "ERROR", style=_state_badge_style(ok))
    if elapsed:
        text.append("  ")
        text.append(elapsed, style="paulo.dim")
    return text


def _dot_style(style: str) -> str:
    if "dim" in style or "thinking" in style:
        return "bold paulo.thinking"
    if "error" in style:
        return "bold paulo.error"
    if "warn" in style or "plan" in style:
        return "bold paulo.warn"
    if "success" in style or "execute" in style:
        return "bold paulo.success"
    return "bold paulo.accent"


def _badge_style(style: str) -> str:
    if "error" in style:
        return "paulo.error paulo.bg.error"
    if "warn" in style or "plan" in style:
        return "paulo.warn paulo.bg.warn"
    if "success" in style or "execute" in style:
        return "paulo.success paulo.bg.ok"
    if "thinking" in style or "dim" in style:
        return "paulo.thinking paulo.bg.muted"
    if "accent" in style:
        return "paulo.accent paulo.bg"
    return "paulo.dim paulo.bg"


def _tool_badge_style(name: str, ok: bool) -> str:
    if not ok:
        return "paulo.error paulo.bg.error"
    normalized = name.lower()
    if normalized in {"write_file", "edit_file"}:
        return "paulo.warn paulo.bg.warn"
    if normalized in {"bash", "background_bash"}:
        return "paulo.tool paulo.bg"
    return "paulo.success paulo.bg.ok"


def _state_badge_style(ok: bool) -> str:
    return "paulo.success paulo.bg.ok" if ok else "paulo.error paulo.bg.error"


def preview_block(text: object, limit: int = 360, style: str = "paulo.dim") -> Padding:
    body = trim(text, limit)
    return Padding(Text(body or "(empty)", style=style), (0, 0, 0, BODY_LEFT))


def user_block(message: str) -> Group:
    body = Text(f" {message.rstrip() or '(empty)'} ", style="paulo.text paulo.bg.muted")
    return Group(Padding(body, (1, 0, 0, BODY_LEFT)))


def notice(title: str, body: str = "", style: str = "paulo.accent") -> Panel:
    return Panel(
        Text(body, style="paulo.text") if body else Text(""),
        title=f" {escape(title)} ",
        title_align="left",
        border_style=style,
        padding=(0, 2),
        expand=False,
    )


def command_table(commands: list[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="paulo.accent.bold", no_wrap=True)
    table.add_column(style="paulo.text")
    for name, help_text in commands:
        table.add_row(name, help_text)
    return table


def key_row(keys: list[tuple[str, str, str]]) -> Text:
    text = Text("  ")
    for index, (key, label, style) in enumerate(keys):
        if index:
            text.append("   ", style="paulo.dim")
        text.append(key, style=f"bold {style}")
        text.append(" ")
        text.append(label, style="paulo.dim")
    return text


def prompt_markup(mode: object) -> str:
    key = getattr(mode, "value", str(mode))
    label, style = MODE_STYLES.get(key, MODE_STYLES["chat"])
    return f"[paulo.faint]paulo[/] [{style}]{label}[/] [paulo.faint]>[/] "


def render_event(label: str, body) -> Group:
    return Group(prefix(label, "paulo.accent"), Padding(body, (0, 0, 0, BODY_LEFT)))
