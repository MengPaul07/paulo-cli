
from ..config import console
from ..core.renderer.tui import key_row, preview_block

from rich.console import Group
from rich.panel import Panel
from rich.text import Text


class HITLGuard:
    SENSITIVE_TOOLS = {"write_file", "edit_file"}
    SENSITIVE_BASH_PATTERNS = ["rm ", "del ", "rmdir", "move ", "mv ", "> "]

    def __init__(self, auto_approve: bool = False):
        self._allowlist: set[tuple[str, str]] = set()
        self._auto = auto_approve  # True=跳过审批，全部直接执行

    def is_sensitive(self, tool_name: str, tool_input: dict) -> bool:
        if self._auto:
            return False  # benchmark 等自动模式跳过审批
        if tool_name in self.SENSITIVE_TOOLS:
            return True
        if tool_name == "bash":
            cmd = tool_input.get("command", "")
            return any(p in cmd for p in self.SENSITIVE_BASH_PATTERNS)
        return False

    def ask(self, tool_name: str, tool_input: dict) -> str:
        if tool_name in ("write_file", "edit_file"):
            target = tool_input.get("path", "?")
            preview = tool_input.get("content", tool_input.get("new_text", ""))
            body = Group(
                Text(tool_name, style="paulo.accent.bold"),
                Text(f"target  {target}", style="paulo.dim"),
                preview_block(preview, 220, "paulo.text"),
                key_row([
                    ("y", "allow once", "paulo.success"),
                    ("a", "always allow", "paulo.success"),
                    ("n", "deny", "paulo.error"),
                ]),
            )
        elif tool_name == "bash":
            body = Group(
                Text(tool_name, style="paulo.accent.bold"),
                Text(f"command  {tool_input.get('command', '?')[:160]}", style="paulo.text"),
                key_row([
                    ("y", "allow once", "paulo.success"),
                    ("a", "always allow", "paulo.success"),
                    ("n", "deny", "paulo.error"),
                ]),
            )
        else:
            body = Group(
                Text(tool_name, style="paulo.accent.bold"),
                preview_block(str(tool_input), 220, "paulo.text"),
                key_row([
                    ("y", "allow once", "paulo.success"),
                    ("a", "always allow", "paulo.success"),
                    ("n", "deny", "paulo.error"),
                ]),
            )

        console.print(
            Panel(
                body,
                title=" approval required ",
                title_align="left",
                border_style="paulo.warn",
                padding=(1, 2),
                expand=False,
            )
        )

        choice = input("  approve > ").strip().lower()

        if choice == "a":
            return "allow_always"
        elif choice == "y":
            return "allow_once"
        return "deny"
    
    def _signature(self, tool_name: str, tool_input: dict) -> str:
        """生成工具调用唯一签名，用于白名单去重。"""
        if tool_name in ("write_file", "edit_file"):
            return f"{tool_name}:{tool_input.get('path', '')}"
        if tool_name == "bash":
            return f"bash:{tool_input.get('command', '')[:80]}"
        return tool_name

    def is_allowlisted(self, tool_name: str, tool_input: dict) -> bool:
        """检查此调用是否已在白名单中。"""
        return (tool_name, self._signature(tool_name, tool_input)) in self._allowlist

    def add_to_allowlist(self, tool_name: str, tool_input: dict):
        """加入白名单，后续同签名调用自动放行。"""
        self._allowlist.add((tool_name, self._signature(tool_name, tool_input)))
