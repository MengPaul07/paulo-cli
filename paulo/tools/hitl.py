
from ..config import console


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
            preview = tool_input.get("content", tool_input.get("new_text", ""))[:80]
            summary = f"目标: {target}\n  预览: {preview}"
        elif tool_name == "bash":
            summary = f"命令: {tool_input.get('command', '?')[:120]}"
        else:
            summary = str(tool_input)[:120]

        console.print(
            f"\n[bold yellow]⚠ 敏感操作需要审批[/bold yellow]\n"
            f"  [bold cyan]{tool_name}[/bold cyan]\n"
            f"  [dim]{summary}[/dim]\n"
            f"\n  [green]y[/green] 允许一次  "
            f"[green]a[/green] 始终允许  "
            f"[red]n[/red] 拒绝"
        )

        choice = input("  ► ").strip().lower()

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