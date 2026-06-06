"""Event-driven Rich renderer for the Paulo coding CLI."""

from __future__ import annotations

import time

from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from ...config import console
from .events import Event, EventType
from .tui import BODY_LEFT, key_row, notice, prefix, preview_block, status_line, tool_line, trim


class RichRenderer:
    """Render agent events as a compact, professional terminal workflow."""

    def __init__(self):
        self._live: Live | None = None
        self._buffer = ""
        self._thinking_buf = ""
        self._t0 = 0.0
        self._char_count = 0
        self._first_turn = True
        self._finalized = False
        self.show_chatroom = False

    def begin_loop(self):
        self._live = Live(
            self._assistant_view(""),
            console=console,
            auto_refresh=False,
            vertical_overflow="visible",
            transient=True,
        )
        self._live.start()
        self._buffer = ""
        self._thinking_buf = ""
        self._finalized = False

    def end_loop(self):
        if self._live:
            self._live.stop()
            self._live = None
        self._buffer = ""

    def handle(self, event: Event):
        if self._live is None:
            self.begin_loop()

        event_type = event.type
        if event_type == EventType.THINKING:
            self._thinking()
        elif event_type == EventType.THINKING_DELTA:
            self._thinking_delta(event)
        elif event_type == EventType.THINKING_DONE:
            self._thinking_done()
        elif event_type == EventType.TEXT_DELTA:
            self._text_delta(event)
        elif event_type == EventType.TEXT_DONE:
            self._text_done()
        elif event_type == EventType.TOOL_RESULT:
            self._tool_result(event)
        elif event_type == EventType.HITL_ASK:
            self._hitl_ask(event)
        elif event_type == EventType.HITL_RESULT:
            self._hitl_result(event)
        elif event_type == EventType.COMPACT:
            self._compact(event)
        elif event_type == EventType.NAG:
            self._nag()
        elif event_type == EventType.TEAMMATE:
            self._teammate(event)
        elif event_type == EventType.INTERRUPT:
            self._interrupt()
        elif event_type == EventType.ERROR:
            self._error(event)

    def _assistant_view(
        self,
        body: str,
        thinking: str = "",
        label: str = "",
        style: str = "paulo.accent",
    ) -> Group:
        renderables = []
        if label:
            renderables.append(prefix(label, style))
        if body.strip():
            # ● 领首行，2 空格统一缩进
            renderables.append(Padding(
                Markdown(f"●  {body.strip()}"), (0, 0, 0, 2)
            ))
        elif thinking:
            renderables.append(Padding(Text(thinking, style="paulo.dim italic"), (0, 0, 0, BODY_LEFT)))
        else:
            renderables.append(Padding(Text("thinking...", style="paulo.dim italic"), (0, 0, 0, BODY_LEFT)))
        return Group(*renderables)

    def _sep(self):
        if not self._first_turn:
            console.print()
            console.print("  [paulo.faint]" + "-" * 48 + "[/]")
        self._first_turn = False

    def _thinking(self):
        self._sep()
        self._buffer = ""
        self._thinking_buf = ""
        self._char_count = 0
        self._t0 = time.time()
        if self._live is None:
            self.begin_loop()
        self._live.update(
            self._assistant_view("", "thinking...", "thinking", "paulo.thinking"),
            refresh=True,
        )

    def _text_delta(self, event: Event):
        if not event.content:
            return
        self._buffer += event.content
        self._char_count += len(event.content)
        self._live.update(self._assistant_view(self._buffer), refresh=True)

    def _text_done(self):
        from ..token_tracker import tracker

        if self._finalized:
            return
        self._finalized = True

        if self._live:
            self._live.stop()
            self._live = None

        if self._buffer.strip():
            console.print()  # 与 thinking 空一行
            console.print(self._assistant_view(self._buffer))

        elapsed = time.time() - self._t0
        est = max(1, self._char_count // 4)
        console.print(
            status_line(
                "usage",
                f"{elapsed:.1f}s | ~{est} tok streamed | {tracker.summary()}",
                "paulo.dim",
            )
        )

    def _thinking_delta(self, event: Event):
        if not event.content:
            return
        self._thinking_buf += event.content
        preview = trim(self._thinking_buf, 220)
        if self._buffer.strip():
            self._live.update(self._assistant_view(self._buffer), refresh=True)
        else:
            self._live.update(
                self._assistant_view("", preview, "thinking", "paulo.thinking"),
                refresh=True,
            )

    def _thinking_done(self):
        elapsed = time.time() - self._t0
        if len(self._thinking_buf) > 30:
            console.print(status_line("thinking", f"{elapsed:.1f}s | {trim(self._thinking_buf, 120)}", "paulo.thinking"))
        self._thinking_buf = ""

    def _tool_result(self, event: Event):
        timing = f"{event.tool_elapsed:.1f}s" if event.tool_elapsed else ""
        console.print(tool_line(event.tool_name, event.tool_ok, timing))
        if event.tool_output:
            console.print(preview_block(event.tool_output, 260))

    def _teammate(self, event: Event):
        action = event.teammate_action
        if action in ("send_message", "idle", "read_inbox") and not self.show_chatroom:
            return
        output = trim(event.tool_output, 120)
        console.print(prefix(f"team {event.teammate_name}", "paulo.accent", action))
        if output:
            console.print(preview_block(output, 160))

    def _hitl_ask(self, event: Event):
        details = [
            Text(event.tool_name, style="paulo.accent.bold"),
        ]
        if event.hitl_target:
            details.append(Text(event.hitl_target, style="paulo.dim"))
        if event.hitl_preview:
            details.append(Text(trim(event.hitl_preview, 240), style="paulo.text"))

        console.print()
        console.print(
            Panel(
                Group(*details, key_row([
                    ("y", "allow once", "paulo.success"),
                    ("a", "always allow", "paulo.success"),
                    ("n", "deny", "paulo.error"),
                ])),
                title=" approval required ",
                title_align="left",
                border_style="paulo.warn",
                padding=(1, 2),
                expand=False,
            )
        )

    def _hitl_result(self, event: Event):
        labels = {
            "deny": ("denied", "paulo.error"),
            "allow_once": ("allowed once", "paulo.dim"),
            "allow_always": ("always allowed", "paulo.success"),
        }
        label, style = labels.get(event.hitl_decision, (event.hitl_decision, "paulo.dim"))
        console.print(status_line("approval", label, style))

    def _compact(self, event: Event):
        reason = "manual" if event.compact_reason == "manual" else "auto"
        console.print(status_line("compact", f"{reason} context compression"))

    def _nag(self):
        console.print(status_line("todo", "open items need an update", "paulo.warn"))

    def _interrupt(self):
        self._buffer = ""
        console.print()
        console.print(notice("interrupted", "Current response was stopped.", "paulo.warn"))

    def _error(self, event: Event):
        console.print(notice("error", event.error_msg, "paulo.error"))
