"""
repl.py —— REPL 交互循环（从 main.py 拆出）

将原本散落在 main() 里的命令分发和模式管理集中为 Repl 类。

用法：
    repl = Repl(history=[], agent_loop_fn=agent_loop, ...)
    repl.run()
"""

import json
from dataclasses import dataclass
from collections.abc import Callable

from ..config import console
from ..core.renderer.tui import command_table, notice, preview_block, prompt_markup, status_line, user_block


# ╔══════════════════════════════════════════════════════════════╗
# ║           命令注册表                                        ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class Command:
    """
    一条 REPL 命令。

    name:    命令前缀（如 "/plan"），匹配时用 startswith
    handler: 处理函数，签名为 (query: str) -> bool
             query 是用户输入的完整字符串
             返回 True 表示继续（命令已处理完），False 表示退出 REPL
    help:    一行帮助文本
    """
    name: str
    handler: Callable[[str], bool]
    help: str


class Commander:
    """
    REPL 主循环。

    run() 进入无限循环：
    1. 显示模式提示符，读取用户输入
    2. 匹配命令 → 调 handler
    3. 未匹配 → 调 agent_loop 走 LLM 对话
    4. Ctrl+C 中断 LLM 但不退出，第二次 Ctrl+C 退出
    """

    def __init__(
        self,
        history: list[dict],
        agent_loop_fn: Callable[[list[dict]], None],
        plans,         # PlanManager
        task_mgr,       # TaskManager
        team,           # TeammateManager
        bus,            # MessageBus
        memory_mgr,     # MemoryManager
        agent_mode_ref: dict,    # {"mode": AgentMode} — 用 dict 包装，可跨模块修改
        active_plan_id_ref: dict, # {"id": int|None}
    ):
        self.history = history
        self.agent_loop = agent_loop_fn
        self._plans = plans
        self._tasks = task_mgr
        self._team = team
        self._bus = bus
        self._memory = memory_mgr
        self._mode = agent_mode_ref
        self._plan_id = active_plan_id_ref
        self._rounds = 0  # 对话轮数计数器，用于自动偏好学习
        self._chatroom = False  # 聊天室模式开关

        # 注册所有 / 命令
        self._commands: list[Command] = self._register()

    # ── 命令注册 ─────────────────────────────────────────────

    def _register(self) -> list[Command]:
        """注册所有 REPL 命令。每 commands = 前缀 + 处理函数 + 帮助文本。"""
        return [
            Command("/tokens",   self._cmd_tokens,   "显示 token 消耗与费用"),
            Command("/plan",     self._cmd_plan,     "进入Plan模式（只读探索 → 产出方案）"),
            Command("/approve",  self._cmd_approve,  "批准待审批Plan，进入执行模式"),
            Command("/reject",   self._cmd_reject,   "拒绝待审批Plan，回到聊天模式"),
            Command("/plans",    self._cmd_plans,    "查看所有Plan历史及统计"),
            Command("/tasks",    self._cmd_tasks,    "查看持久任务列表"),
            Command("/team",     self._cmd_team,     "查看队友状态"),
            Command("/memory",   self._cmd_memory,   "记忆管理（全部走 LLM 解析意图）"),
            Command("/inbox",    self._cmd_inbox,    "查看主代理收件箱"),
            Command("/help",     self._cmd_help,     "显示所有可用命令"),
            Command("/clear",    self._cmd_clear,    "清除对话历史（开始新对话）"),
            Command("/compact",  self._cmd_compact,  "compact对话历史"),
            Command("/chatroom", self._cmd_chatroom, "切换聊天室模式"),
        ]

    # ── 匹配 ─────────────────────────────────────────────────

    def _match(self, query: str) -> Command | None:
        """按前缀匹配命令。多 commands共享前缀时取最长匹配。"""
        for cmd in sorted(self._commands, key=lambda c: len(c.name), reverse=True):
            if query.startswith(cmd.name):
                return cmd
        return None

    # ── 提示符 ───────────────────────────────────────────────

    def _prompt(self) -> str:
        """Return the Rich markup prompt for the current mode."""
        return prompt_markup(self._mode.get("mode", "chat"))

    # ── Agent 调度 + 偏好学习 ────────────────────────────────

    def _run_agent(self):
        """调 agent_loop + 每 3 轮自动学习偏好。"""
        self.agent_loop(self.history)
        self._rounds += 1
        if self._rounds % 3 == 0 and len(self.history) > 5:
            from ..core.memory.manager import learn_from_session
            learn_from_session(self.history, self._memory)

    # ── 主循环 ───────────────────────────────────────────────

    def run(self):
        """
        进入 REPL 主循环。

        退出方式：输入 q / exit / quit 或 Ctrl+C。
        Ctrl+C 在 agent_loop 执行期间只中断当前操作，不退出。
        """
        while True:
            try:
                query = console.input(self._prompt())
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            stripped = query.strip()

            # 退出
            if stripped.lower() in ("", "q", "exit", "quit"):
                break

            # 匹配 / 命令
            matched = self._match(stripped)
            if matched:
                try:
                    matched.handler(stripped)
                except Exception as e:
                    console.print(notice("command error", str(e), "paulo.error"))
                continue

            # 默认：LLM 对话
            self.history.append({"role": "user", "content": query})
            try:
                self._run_agent()
            except KeyboardInterrupt:
                console.print()
                console.print(notice("interrupted", "Current operation was stopped.", "paulo.warn"))
                # 清除被中断的半条回复
                if self.history and self.history[-1].get("role") == "assistant":
                    self.history.pop()
            console.print()

    # ═══════════════════════════════════════════════════════════
    #  命令处理函数（每个对应一条 / 命令）
    #  签名: (query: str) -> bool   返回 True 继续，False 退出
    # ═══════════════════════════════════════════════════════════

    # ── /plan ────────────────────────────────────────────────

    def _cmd_plan(self, query: str) -> bool:
        """
        /plan <任务描述>  — 进入Plan模式。
        /plan show <id>  — 查看历史Plan详情。
        """
        args = query[len("/plan"):].strip()

        # 子命令：/plan show <id>
        if args.startswith("show "):
            try:
                plan_id = int(args.split()[-1])
                console.print(preview_block(self._plans.get(plan_id).detail(), 4000, "paulo.text"))
            except (ValueError, IndexError):
                console.print(status_line("usage", "/plan show <id>", "paulo.warn"))
            except ValueError as e:
                console.print(notice("error", str(e), "paulo.error"))
            return True

        # 主命令：/plan <任务描述>
        if not args:
            console.print(status_line("usage", "/plan <task>", "paulo.warn"))
            return True

        self._mode["mode"] = "plan"
        self._plan_id["id"] = None
        console.print(status_line("mode", "PLAN | readonly exploration", "paulo.plan"))

        self.history.append({"role": "user", "content": args})
        self._run_agent()

        # 提取 LLM 输出的方案文本
        last_msg = self.history[-1].get("content")
        if isinstance(last_msg, list):
            plan_text = "\n".join(
                b.text for b in last_msg if hasattr(b, "text")
            )
        else:
            plan_text = str(last_msg)

        plan_title = args[:40] + ("..." if len(args) > 40 else "")
        plan = self._plans.create(title=plan_title, content=plan_text)
        self._plan_id["id"] = plan.id

        console.print()
        console.print(
            notice(
                f"plan #{plan.id} saved",
                "/approve to execute | /reject to return to chat",
                "paulo.plan",
            )
        )
        return True

    # ── /approve ─────────────────────────────────────────────

    def _cmd_approve(self, _query: str) -> bool:
        """批准最近一个待审批Plan，进入执行模式。"""
        plan = None
        pid = self._plan_id.get("id")
        if pid is not None:
            try:
                plan = self._plans.get(pid)
            except ValueError:
                pass

        if plan is None or plan.status != "pending":
            plan = self._plans.latest_pending()

        if plan is None:
            console.print(status_line("plan", "no pending plan", "paulo.dim"))
            return True

        plan = self._plans.approve(plan.id)
        self._plan_id["id"] = plan.id
        self._mode["mode"] = "execute"
        console.print(status_line("plan", f"#{plan.id} approved | EXEC mode", "paulo.success"))

        self.history.append({
            "role": "user",
            "content": (
                f"方案已批准。请先根据以下方案内容创建 TodoWrite 清单，"
                f"然后逐条执行：\n\n{plan.content}"
            ),
        })
        self._run_agent()

        try:
            self._plans.mark_executed(plan.id)
            console.print(status_line("plan", f"#{plan.id} marked executed", "paulo.success"))
        except ValueError:
            pass

        self._mode["mode"] = "chat"
        self._plan_id["id"] = None
        return True

    # ── /reject ──────────────────────────────────────────────

    def _cmd_reject(self, _query: str) -> bool:
        """拒绝最近一个待审批Plan。"""
        plan = None
        pid = self._plan_id.get("id")
        if pid is not None:
            try:
                plan = self._plans.get(pid)
            except ValueError:
                pass

        if plan is None or plan.status != "pending":
            plan = self._plans.latest_pending()

        if plan is None:
            console.print(status_line("plan", "no pending plan", "paulo.dim"))
            return True

        self._plans.reject(plan.id)
        self._mode["mode"] = "chat"
        self._plan_id["id"] = None
        console.print(status_line("plan", f"#{plan.id} rejected | CHAT mode", "paulo.warn"))
        return True

    # ── /plans ───────────────────────────────────────────────

    def _cmd_plans(self, _query: str) -> bool:
        """查看所有Plan历史。"""
        console.print(preview_block(self._plans.list_all(), 4000, "paulo.text"))
        return True

    # ── /tasks ───────────────────────────────────────────────

    def _cmd_tasks(self, _query: str) -> bool:
        """查看持久任务列表。"""
        console.print(preview_block(self._tasks.list_all(), 4000, "paulo.text"))
        return True

    # ── /team ────────────────────────────────────────────────

    def _cmd_team(self, _query: str) -> bool:
        """查看队友状态。"""
        console.print(preview_block(self._team.list_all(), 4000, "paulo.text"))
        return True

    # ── /memory ──────────────────────────────────────────────

    def _cmd_memory(self, query: str) -> bool:
        """
        /memory          — 列出记忆
        /memory <内容>   — LLM 解析意图，保存/查询/删除
        全部走 LLM，不直接调 MemoryManager。
        """
        args = query[len("/memory"):].strip()
        if not args:
            prompt = "请用 read_file 读取 .memory/MEMORY.md 展示所有记忆。"
        else:
            prompt = f"请用 read_file/write_file/edit_file 操作 .memory/ 目录完成记忆操作：{args}"

        self.history.append({"role": "user", "content": prompt})
        self._run_agent()
        return True

    # ── /inbox ───────────────────────────────────────────────

    def _cmd_inbox(self, _query: str) -> bool:
        """查看 lead 的收件箱。"""
        inbox_content = self._bus.read_inbox("lead")
        if inbox_content:
            console.print(preview_block(json.dumps(inbox_content, indent=2, ensure_ascii=False), 4000, "paulo.text"))
        else:
            console.print(status_line("inbox", "empty"))
        return True

    # ── /compact ─────────────────────────────────────────────

    def _cmd_compact(self, _query: str) -> bool:
        """compact对话历史。"""
        if not self.history:
            console.print(status_line("compact", "no history"))
            return True
        from ..core.compression import auto_compact  # 延迟导入避免循环
        console.print(status_line("compact", "compressing conversation"))
        self.history[:] = auto_compact(self.history)
        return True

    # ── /chatroom ────────────────────────────────────────────

    def _cmd_chatroom(self, _query: str) -> bool:
        """切换聊天室模式——开启后显示所有 Agent 间消息。"""
        self._chatroom = not self._chatroom
        import paulo.main as _pm
        if _pm._renderer:
            _pm._renderer.show_chatroom = self._chatroom
        state = "on" if self._chatroom else "off"
        style = "paulo.success" if self._chatroom else "paulo.dim"
        console.print(status_line("chatroom", state, style))
        return True

    # ── /help ────────────────────────────────────────────────

    def _cmd_help(self, _query: str) -> bool:
        """列出所有可用命令及说明。"""
        console.print(command_table([(cmd.name, cmd.help) for cmd in self._commands]))
        console.print(status_line("commands", str(len(self._commands))))
        return True

    # ── /clear ───────────────────────────────────────────────

    def _cmd_clear(self, _query: str) -> bool:
        """清除对话历史，开始新对话。"""
        self.history.clear()
        console.print(status_line("history", "cleared"))
        return True
    # ── /tokens ───────────────────────────────────────────────

    def _cmd_tokens(self, _query: str) -> bool:
        """显示 token 消耗统计。"""
        from ..core.token_tracker import tracker
        console.print(preview_block(tracker.detailed(), 1000, "paulo.text"))
        return True
