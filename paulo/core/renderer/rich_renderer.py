"""
Rich 渲染器 —— Claude Code 风格终端 UI

色板:
  cyan bold    工具名、交互提示
  green        成功状态
  yellow       警告、HITL 审批
  dim (#888)   元数据、预览、分隔线
  white        正文、主标题

排版:
  工具日志:     ╶ tool_name · 0.3s
               预览内容（dim, 最多 200 字）
  HITL:         ┌ 黄色标题
                │ 目标 + 预览
                └ 选项
  流式正文:     Markdown（代码高亮、表格、列表）
  状态:         · compact / · interrupted（dim）
"""

from rich.markdown import Markdown
from rich.live import Live

from ...config import console
from .events import Event, EventType


class RichRenderer:
    """
    事件驱动渲染器。

    begin_loop() / end_loop() 控制 Live 的启停——
    Live 只在 agent_loop 执行期间活跃，REPL 等输入时不占用终端。
    """

    def __init__(self):
        self._live: Live | None = None
        self._buffer = ""

    # ── 生命周期 ──────────────────────────────────────────────

    def begin_loop(self):
        """agent_loop 开始前调用，启动 Live 渲染区。"""
        self._live = Live(
            Markdown(""), console=console,
            auto_refresh=False, vertical_overflow="visible",
        )
        self._live.start()
        self._buffer = ""

    def end_loop(self):
        """agent_loop 结束前调用，释放终端。"""
        if self._live:
            self._live.stop()
            self._live = None
        self._buffer = ""

    # ── 分发 ──────────────────────────────────────────────────

    def handle(self, event: Event):
        if self._live is None:
            self.begin_loop()  # 容错：如果没调 begin_loop，自动开

        t = event.type
        if t == EventType.THINKING:       self._thinking()
        elif t == EventType.TEXT_DELTA:    self._text_delta(event)
        elif t == EventType.TEXT_DONE:     pass
        elif t == EventType.TOOL_RESULT:   self._tool_result(event)
        elif t == EventType.HITL_ASK:      self._hitl_ask(event)
        elif t == EventType.HITL_RESULT:   self._hitl_result(event)
        elif t == EventType.COMPACT:       self._compact(event)
        elif t == EventType.NAG:           self._nag()
        elif t == EventType.INTERRUPT:     self._interrupt()
        elif t == EventType.ERROR:         self._error(event)

    # ── LLM 流式 ──────────────────────────────────────────────

    def _thinking(self):
        """每轮 LLM 调用前重置缓冲区。"""
        self._buffer = ""
        if self._live is None:
            self.begin_loop()
        self._live.update(Markdown("*Thinking…*"), refresh=True)

    def _text_delta(self, event: Event):
        if event.content:
            self._buffer += event.content
            self._live.update(Markdown(self._buffer), refresh=True)

    # ── 工具 ──────────────────────────────────────────────────

    def _tool_result(self, event: Event):
        """工具日志 —— cyan 工具名 + 耗时 + dim 预览。"""
        timing = f" [dim]· {event.tool_elapsed:.1f}s[/dim]" if event.tool_elapsed else ""
        console.print(
            f"  [dim]>[/dim] [bold cyan]{event.tool_name}[/bold cyan]{timing}"
        )
        if event.tool_output:
            # 预览最多 200 字，多行时取首行
            preview = event.tool_output.replace("\n", " ")[:200]
            console.print(f"  [dim]{preview}[/dim]")

    # ── HITL ───────────────────────────────────────────────────

    def _hitl_ask(self, event: Event):
        console.print()
        console.print(
            f"  [bold yellow]审批[/bold yellow]  [bold cyan]{event.tool_name}[/bold cyan]"
        )
        if event.hitl_target:
            console.print(f"  [dim]目标:[/dim] {event.hitl_target}")
        if event.hitl_preview:
            console.print(f"  [dim]{event.hitl_preview[:200]}[/dim]")
        console.print(
            f"  [green]y[/green] 允许  "
            f"[green]a[/green] 始终允许  "
            f"[red]n[/red] 拒绝"
        )

    def _hitl_result(self, event: Event):
        labels = {"deny": "[red]已拒绝[/red]", "allow_once": "[dim]已放行[/dim]",
                  "allow_always": "[green]已授权[/green]"}
        console.print(f"  {labels.get(event.hitl_decision, event.hitl_decision)}")

    # ── 系统状态 ──────────────────────────────────────────────

    def _compact(self, event: Event):
        reason = "手动压缩" if event.compact_reason == "manual" else "自动压缩"
        console.print(f"  [dim]· {reason}[/dim]")

    def _nag(self):
        console.print(f"  [dim]· 待办提醒：你有未完成的 Todo 项[/dim]")

    def _interrupt(self):
        self._buffer = ""
        console.print(f"\n  [yellow]· 已中断[/yellow]")

    def _error(self, event: Event):
        console.print(f"  [red]✗ {event.error_msg}[/red]")
