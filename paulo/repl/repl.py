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


class Repl:
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

        # 注册所有 / 命令
        self._commands: list[Command] = self._register()

    # ── 命令注册 ─────────────────────────────────────────────

    def _register(self) -> list[Command]:
        """注册所有 REPL 命令。每条命令 = 前缀 + 处理函数 + 帮助文本。"""
        return [
            Command("/plan",     self._cmd_plan,     "进入计划模式（只读探索 → 产出方案）"),
            Command("/approve",  self._cmd_approve,  "批准待审批计划，进入执行模式"),
            Command("/reject",   self._cmd_reject,   "拒绝待审批计划，回到聊天模式"),
            Command("/plans",    self._cmd_plans,    "查看所有计划历史及统计"),
            Command("/tasks",    self._cmd_tasks,    "查看持久任务列表"),
            Command("/team",     self._cmd_team,     "查看队友状态"),
            Command("/memory",   self._cmd_memory,   "记忆管理（全部走 LLM 解析意图）"),
            Command("/inbox",    self._cmd_inbox,    "查看主代理收件箱"),
            Command("/help",     self._cmd_help,     "显示所有可用命令"),
            Command("/clear",    self._cmd_clear,    "清除对话历史（开始新对话）"),
            Command("/compact",  self._cmd_compact,  "手动压缩对话历史"),
        ]

    # ── 匹配 ─────────────────────────────────────────────────

    def _match(self, query: str) -> Command | None:
        """按前缀匹配命令。多条命令共享前缀时取最长匹配。"""
        for cmd in sorted(self._commands, key=lambda c: len(c.name), reverse=True):
            if query.startswith(cmd.name):
                return cmd
        return None

    # ── 提示符 ───────────────────────────────────────────────

    def _prompt(self) -> str:
        """根据当前模式返回不同颜色的 ANSI 提示符。"""
        mode = self._mode.get("mode", "chat")
        prompts = {
            "chat":    "\033[36ms_full >> \033[0m",    # 青色
            "plan":    "\033[33m[PLAN] >> \033[0m",    # 黄色
            "execute": "\033[32m[EXEC] >> \033[0m",    # 绿色
        }
        return prompts.get(mode, "\033[36ms_full >> \033[0m")

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
                query = input(self._prompt())
            except (EOFError, KeyboardInterrupt):
                print()
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
                    console.print(f"[red]命令执行异常: {e}[/red]")
                continue

            # 默认：LLM 对话
            self.history.append({"role": "user", "content": query})
            try:
                self._run_agent()
            except KeyboardInterrupt:
                console.print("\n[yellow][已中断][/yellow]")
                # 清除被中断的半条回复
                if self.history and self.history[-1].get("role") == "assistant":
                    self.history.pop()
            print()

    # ═══════════════════════════════════════════════════════════
    #  命令处理函数（每个对应一条 / 命令）
    #  签名: (query: str) -> bool   返回 True 继续，False 退出
    # ═══════════════════════════════════════════════════════════

    # ── /plan ────────────────────────────────────────────────

    def _cmd_plan(self, query: str) -> bool:
        """
        /plan <任务描述>  — 进入计划模式。
        /plan show <id>  — 查看历史计划详情。
        """
        args = query[len("/plan"):].strip()

        # 子命令：/plan show <id>
        if args.startswith("show "):
            try:
                plan_id = int(args.split()[-1])
                print(self._plans.get(plan_id).detail())
            except (ValueError, IndexError):
                print("用法: /plan show <计划ID>")
            except ValueError as e:
                print(f"错误: {e}")
            return True

        # 主命令：/plan <任务描述>
        if not args:
            print("用法: /plan <任务描述>")
            return True

        self._mode["mode"] = "plan"
        self._plan_id["id"] = None
        console.print("[yellow][已进入计划模式，工具仅限于读取和探索][/yellow]")

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

        print(f"\n{'─' * 40}")
        print(f"[计划 #{plan.id} 已生成并保存]")
        print("  /approve — 批准计划，进入执行模式")
        print("  /reject  — 拒绝计划，回到聊天模式")
        return True

    # ── /approve ─────────────────────────────────────────────

    def _cmd_approve(self, _query: str) -> bool:
        """批准最近一个待审批计划，进入执行模式。"""
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
            console.print("[dim](没有待审批的计划)[/dim]")
            return True

        plan = self._plans.approve(plan.id)
        self._plan_id["id"] = plan.id
        self._mode["mode"] = "execute"
        console.print(f"[green][计划 #{plan.id} 已批准，进入执行模式][/green]")

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
            console.print(f"[green][计划 #{plan.id} 已标记为已执行][/green]")
        except ValueError:
            pass

        self._mode["mode"] = "chat"
        self._plan_id["id"] = None
        return True

    # ── /reject ──────────────────────────────────────────────

    def _cmd_reject(self, _query: str) -> bool:
        """拒绝最近一个待审批计划。"""
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
            console.print("[dim](没有待审批的计划)[/dim]")
            return True

        self._plans.reject(plan.id)
        self._mode["mode"] = "chat"
        self._plan_id["id"] = None
        console.print(f"[yellow][计划 #{plan.id} 已拒绝，已返回聊天模式][/yellow]")
        return True

    # ── /plans ───────────────────────────────────────────────

    def _cmd_plans(self, _query: str) -> bool:
        """查看所有计划历史。"""
        print(self._plans.list_all())
        return True

    # ── /tasks ───────────────────────────────────────────────

    def _cmd_tasks(self, _query: str) -> bool:
        """查看持久任务列表。"""
        print(self._tasks.list_all())
        return True

    # ── /team ────────────────────────────────────────────────

    def _cmd_team(self, _query: str) -> bool:
        """查看队友状态。"""
        print(self._team.list_all())
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
            print(json.dumps(inbox_content, indent=2, ensure_ascii=False))
        else:
            print("(收件箱为空)")
        return True

    # ── /compact ─────────────────────────────────────────────

    def _cmd_compact(self, _query: str) -> bool:
        """手动压缩对话历史。"""
        if not self.history:
            print("(暂无对话历史)")
            return True
        from ..core.compression import auto_compact  # 延迟导入避免循环
        print("[手动压缩]")
        self.history[:] = auto_compact(self.history)
        return True

    # ── /help ────────────────────────────────────────────────

    def _cmd_help(self, _query: str) -> bool:
        """列出所有可用命令及说明。"""
        for cmd in self._commands:
            console.print(f"  [bold cyan]{cmd.name:<12}[/bold cyan] {cmd.help}")
        console.print(f"\n  [dim]共 {len(self._commands)} 条命令[/dim]")
        return True

    # ── /clear ───────────────────────────────────────────────

    def _cmd_clear(self, _query: str) -> bool:
        """清除对话历史，开始新对话。"""
        self.history.clear()
        console.print("[dim]对话历史已清除[/dim]")
        return True
