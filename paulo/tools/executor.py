"""
tool_executor.py —— 工具执行组件

将 agent_loop 第六步的执行逻辑封装为 ToolExecutor 类。
依赖：HITLGuard（审批） + handlers（工具分发映射）。

用法：
    executor = ToolExecutor(TOOL_HANDLERS, HITLGuard())

    for block in final_message.content:
        if block.type != "tool_use":
            continue
        result = executor.execute(block)
        console.print(f"  [dim]>[/dim] [bold cyan]{block.name}[/bold cyan]: ...")
        tool_results.append(result)

execute() 只负责"判断 + 执行"，日志由调用方打。
"""

from .hitl import HITLGuard


class ToolExecutor:
    """工具执行器——查 handler → HITL 审批 → 执行 → 返回结果 dict。"""

    def __init__(self, handlers: dict, hitl_guard: HITLGuard):
        """
        Args:
            handlers:   工具名 → 处理函数的映射（由 tools_registry.build_handlers 产出）
            hitl_guard: HITLGuard 审批门禁实例
        """
        self._handlers = handlers
        self._hitl = hitl_guard

    def execute(self, block) -> dict:
        """
        执行一个 Anthropic tool_use block。

        流程：
        1. 查 handler — 没有则返回错误
        2. 敏感操作？ → 查白名单 → 未命中则弹审批框
        3. 审批通过后执行 handler
        4. 将结果包成 tool_result dict 返回

        Returns:
            {"type": "tool_result", "tool_use_id": block.id, "content": str}
        """
        # ── 1. 查找处理函数 ─────────────────────────────────
        handler = self._handlers.get(block.name)
        if not handler:
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"未知工具: {block.name}",
            }

        # ── 2. HITL 审批门禁 ────────────────────────────────
        if self._hitl.is_sensitive(block.name, block.input):
            if not self._hitl.is_allowlisted(block.name, block.input):
                decision = self._hitl.ask(block.name, block.input)
                if decision == "deny":
                    return {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "用户拒绝了此操作",
                    }
                elif decision == "allow_always":
                    self._hitl.add_to_allowlist(block.name, block.input)
                # "allow_once": 放行，不加白名单

        # ── 3. 执行工具 ─────────────────────────────────────
        try:
            output = handler(**block.input)
        except Exception as e:
            output = f"工具执行异常: {e}"

        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": str(output),
        }
