#!/usr/bin/env python3
"""
main.py —— 多 Agent 编排 CLI 入口

┌──────────────────────────────────────────────────────────────────┐
│                        架构总览                                   │
│                                                                   │
│  main.py          ── REPL + Agent 主循环 + 工具注册 + System Prompt │
│  config.py        ── 环境变量、路径常量、LLM 客户端                 │
│  tools.py         ── 基础工具：bash, read, write, edit              │
│  tasks.py         ── TodoManager (会话级) + TaskManager (持久级)    │
│  agents.py        ── Subagent + TeammateManager + 关机/计划协议     │
│  messaging.py     ── MessageBus (消息总线) + BackgroundManager       │
│  skills.py        ── SkillLoader (技能加载)                         │
│  compression.py   ── microcompact + auto_compact (对话压缩)         │
│                                                                   │
│  依赖关系（无循环）：                                               │
│  main → config, tools, tasks, agents, messaging, skills, compression│
│  agents → config, tools, messaging, tasks                          │
│  messaging → config                                                │
│  tasks → config                                                    │
│  tools → config                                                    │
│  compression → config                                              │
│  skills → config                                                   │
└──────────────────────────────────────────────────────────────────┘

内置 REPL 命令：
  /plan <任务>  — 进入计划模式（只读探索 → 产出计划 → 持久化到 .plans/）
  /approve     — 批准待审批计划，进入执行模式
  /reject      — 拒绝待审批计划，回到聊天模式
  /plans       — 查看所有计划历史及统计
  /plan show <id> — 查看指定计划的完整详情
  /compact  — 手动触发对话压缩
  /tasks    — 查看持久任务列表
  /team     — 查看队友状态
  /inbox    — 查看主代理收件箱
  q / exit / Enter — 退出
  Ctrl+C — 中断当前 LLM 回复/工具执行（不退出 REPL）
"""

import io
import json
import sys
import time
from enum import Enum

from .config import WORKDIR, SKILLS_DIR, TOKEN_THRESHOLD, client, MODEL, console
from .core.plan.tasks import TodoManager, TaskManager
from .core.multi_agent.agents import (
    run_subagent,
    handle_shutdown_request,
    handle_plan_review,
    TeammateManager,
)
from .core.multi_agent.messaging import BackgroundManager, MessageBus
from .core.skills.loader import SkillLoader
from .core.compression import estimate_tokens, microcompact, auto_compact
from .core.memory.manager import MemoryManager, MEMORY_DIR
from .tools.registry import TOOLS, build_readonly_tools, build_handlers
from .core.plan.plans import PlanManager, PLANS_DIR

# ── 全局实例（模块单例，所有模块共享同一份状态）─────────────────
TODO = TodoManager()                     # 内存待办清单
SKILLS = SkillLoader(SKILLS_DIR)          # 技能加载器（延迟扫描）
TASK_MGR = TaskManager()                  # 持久化任务管理器
BG = BackgroundManager()                  # 后台命令执行器
BUS = MessageBus()                        # 消息总线
TEAM = TeammateManager(BUS, TASK_MGR)     # 队友管理器
PLANS = PlanManager()                     # 计划管理器（文件持久化）
MEMORY = MemoryManager()                  # 记忆管理器（.memory/*.md）


# ╔══════════════════════════════════════════════════════════════╗
# ║           Plan 模式 —— 先计划后执行的审批门禁                  ║
# ╚══════════════════════════════════════════════════════════════╝

class AgentMode(str, Enum):
    """
    Agent 的三种运作模式。

    CHAT    —— 默认聊天模式，全工具可用，自由交互
    PLAN    —— 计划模式，只读探索 → 输出计划 → 等待用户审批
    EXECUTE —— 执行模式，计划已获批，放开写权限按计划实施
    """
    CHAT = "chat"
    PLAN = "plan"
    EXECUTE = "execute"

# 当前模式（REPL 和 agent_loop 共享此状态）
agent_mode: AgentMode = AgentMode.CHAT

# 当前活跃的计划 ID（plan 模式产出的计划，供 /approve /reject 定位）
_active_plan_id: int | None = None


# ╔══════════════════════════════════════════════════════════════╗
# ║                     System Prompt                           ║
# ╚══════════════════════════════════════════════════════════════╝

# ── System Prompt 基座（所有模式共用）───────────────────────────
_SYSTEM_BASE = f"""你是一个 coding agent，工作目录: {WORKDIR}。
使用提供的工具来解决用户的任务。

工具职责区分（重要）：
- task_create / task_update / task_list：你自己做长线工作时，用来拆解和跟踪进度。
  这是你内部的看板，用户不需要关心。
- TodoWrite：当前这一轮对话里的细粒度步骤跟踪（类似便签）。
- task（启动子代理）：需要并行或隔离探索时才用。
- load_skill：加载领域专业知识。

Plan 与 Task 的区别：
- Plan 是给用户看的方案，需要审批。用 /plan 进入计划模式产出。
- Task 是你自己管理复杂工作的工具，不需要用户审批。

可用的技能: {SKILLS.descriptions()}
操作 .paulo/memory/ 用已有文件工具：
- 查看全部: read_file(".paulo/memory/MEMORY.md")
- 新建/覆盖: write_file(".paulo/memory/{{type}}-{{slug}}.md", frontmatter+正文)
- name 必须用 {{type}}-{{slug}} 格式，type 必选其一:
  user 反馈 project reference
  示例: user-pytest、feedback-no-comments、project-deadline
- 更新记忆后必须同步更新 MEMORY.md 索引
- 新增记忆前先读 MEMORY.md，同主题内容追加到已有文件"""

# 默认聊天模式 — 全工具可用
SYSTEM = _SYSTEM_BASE

# 计划模式 — 强调"只探索，不动手"，引导 LLM 产出结构化计划
PLAN_SYSTEM = _SYSTEM_BASE + """
计划模式。你的方案就是 TodoWrite——直接用待办清单写执行步骤。

流程：
1. read_file / bash 探索代码（最多 3-5 轮）
2. 调 TodoWrite 写出步骤 —— 这就是你的方案
3. 补充文字：影响文件、风险

用户审批后按 TodoWrite 逐条执行。"""

EXECUTE_SYSTEM = _SYSTEM_BASE + """
执行模式，所有工具开放。按上次 TodoWrite 的方案逐条执行，
完成一步更新一条。需要调整方案时先说明再改。"""


# ╔══════════════════════════════════════════════════════════════╗
# ║           工具注册（从 tools_registry 导入）                  ║
# ╚══════════════════════════════════════════════════════════════╝

# TOOLS 是纯 JSON Schema 数据，直接从 tools_registry 导入（无运行时依赖）
# TOOL_HANDLERS 通过依赖注入构建：传入所有全局实例，确保无循环引用
TOOL_HANDLERS = build_handlers(
    todo=TODO,
    skills=SKILLS,
    task_mgr=TASK_MGR,
    bg=BG,
    bus=BUS,
    team=TEAM,
    run_subagent_fn=run_subagent,
    handle_shutdown_fn=handle_shutdown_request,
    handle_plan_review_fn=handle_plan_review,
)

# Plan 模式只读工具集 —— 排除写操作（write_file / edit_file）
READONLY_TOOLS = build_readonly_tools()

# ── MCP 外部工具注入 ──────────────────────────────────────
from .core.mcp.client import MCPManager
_mcp = MCPManager()
_mcp_count = _mcp.connect_all()
if _mcp_count > 0:
    # 合并外部工具到 Paulo 工具集
    TOOLS = list(TOOLS) + _mcp.get_tools()
    TOOL_HANDLERS = dict(TOOL_HANDLERS) | _mcp.get_handlers()
    READONLY_TOOLS = build_readonly_tools()  # 重建（MCP 工具只在全工具模式下可用）
    console.print(f"[dim]MCP: {_mcp_count}  servers,  {len(TOOLS)}  tools[/dim]")


from .tools.executor import ToolExecutor
from .tools.hitl import HITLGuard

# 工具执行器（HITL 审批已内嵌在 execute 方法中）
executor = ToolExecutor(TOOL_HANDLERS, HITLGuard())


# ╔══════════════════════════════════════════════════════════════╗
# ║                Agent 主循环 (Agent Loop)                     ║
# ╚══════════════════════════════════════════════════════════════╝

def agent_loop(messages: list, on_event: callable = None):
    """
    Agent 主循环 —— LLM 调用的编排核心。

    Args:
        messages: 当前对话历史列表（原地修改）
        on_event: 渲染回调，签名为 (Event) -> None，传 None 则直接打印
    """
    if on_event is None:
        import paulo.main as _pm
        _renderer = getattr(_pm, "_renderer", None)
        if _renderer is not None:
            _renderer.begin_loop()
            on_event = _renderer.handle
        else:
            def on_event(e): pass

    from .core.renderer.events import Event, EventType
    # 根据当前模式选择工具集和系统提示
    # PLAN 模式：只读工具 + 计划 System Prompt
    # EXECUTE/CHAT 模式：全工具 + 对应的 System Prompt
    if agent_mode == AgentMode.PLAN:
        active_tools = READONLY_TOOLS
        active_system = PLAN_SYSTEM
    elif agent_mode == AgentMode.EXECUTE:
        active_tools = TOOLS
        active_system = EXECUTE_SYSTEM
    else:
        active_tools = TOOLS
        active_system = SYSTEM

    # 动态追加记忆——每次 agent_loop 调用时重新读，新增记忆立即生效
    memory_block = MEMORY.descriptions()
    if memory_block:
        active_system = active_system + "\n\n" + memory_block

    # 跟踪连续未使用 TodoWrite 的轮数，用于 nag 提醒
    rounds_without_todo = 0

    while True:
        # ── 第一步：微压缩（降低 token 消耗）─────────────────
        microcompact(messages)

        # ── 第二步：检查是否需要自动压缩 ─────────────────────
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            on_event(Event(type=EventType.COMPACT, compact_reason="auto"))
            messages[:] = auto_compact(messages)

        # ── 第三步：消费后台任务通知 ─────────────────────────
        notifs = BG.drain()
        if notifs:
            notif_text = "\n".join(
                f"[后台:{n['task_id']}] {n['status']}: {n['result']}"
                for n in notifs
            )
            messages.append({
                "role": "user",
                "content": f"<background-results>\n{notif_text}\n</background-results>",
            })

        # ── 第四步：检查主代理收件箱 ─────────────────────────
        inbox = BUS.read_inbox("lead")
        if inbox:
            messages.append({
                "role": "user",
                "content": f"<inbox>{json.dumps(inbox, indent=2, ensure_ascii=False)}</inbox>",
            })

        # ── 第五步：流式调用 LLM ───────────────────────────
        # SDK 的 stream() 负责逐 token 产出数据
        # rich 的 Live + Markdown 负责实时渲染到终端
        # Ctrl+C 在流式输出期间被捕获 → 中断 LLM 回复但不崩溃
        try:
            with client.messages.stream(
                model=MODEL,
                system=active_system,
                messages=messages,
                tools=active_tools,
                max_tokens=8000,
            ) as stream:
                on_event(Event(type=EventType.THINKING))
                in_thinking = False
                thinking_fired = True  # THINKING 只发一次
                for event in stream:
                    if event.type == "content_block_start":
                        if getattr(event.content_block, "type", "") == "thinking":
                            in_thinking = True
                        else:
                            in_thinking = False
                    elif event.type == "content_block_delta":
                        if in_thinking and getattr(event.delta, "type", "") == "thinking_delta":
                            on_event(Event(type=EventType.THINKING_DELTA,
                                           content=getattr(event.delta, "thinking", "")))
                        elif getattr(event.delta, "type", "") == "text_delta":
                            in_thinking = False
                            on_event(Event(type=EventType.TEXT_DELTA,
                                           content=getattr(event.delta, "text", "")))
                    elif event.type == "content_block_stop" and in_thinking:
                        in_thinking = False
                        on_event(Event(type=EventType.THINKING_DONE))
                final_message = stream.get_final_message()
                # 记录 token 消耗
                from .core.token_tracker import tracker
                tracker.add(final_message.usage)
        except KeyboardInterrupt:
            on_event(Event(type=EventType.INTERRUPT))
            raise

        # 将完整消息追加到对话历史
        messages.append({
            "role": "assistant",
            "content": final_message.content,
        })

        # 模型不再请求工具 → 任务完成
        if final_message.stop_reason != "tool_use":
            on_event(Event(type=EventType.TEXT_DONE))
            import paulo.main as _pm
            if _pm._renderer: _pm._renderer.end_loop()
            return

        # ── 第六步：执行本轮所有工具调用 ─────────────────────
        tool_results = []
        used_todo = False
        manual_compress = False

        try:
            for block in final_message.content:
                if block.type != "tool_use":
                    continue

                # compress 是特殊标记：由主循环接管执行
                if block.name == "compress":
                    manual_compress = True

                # ToolExecutor 统一执行（内含 HITL 审批）
                t_tool = time.time()
                result = executor.execute(block)
                on_event(Event(
                    type=EventType.TOOL_RESULT,
                    tool_name=block.name,
                    tool_output=str(result.get("content", "")),
                    tool_elapsed=time.time() - t_tool,
                ))
                tool_results.append(result)

                if block.name == "TodoWrite":
                    used_todo = True
        except KeyboardInterrupt:
            on_event(Event(type=EventType.INTERRUPT))
            tool_results.append({
                "type": "text",
                "text": "<interrupted>用户中断了工具执行。</interrupted>",
            })

        # ── 第七步：TodoWrite 遗漏提醒（s03 nag 机制）─────
        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            on_event(Event(type=EventType.NAG))
            tool_results.append({
                "type": "text",
                "text": "<reminder>你有未完成的待办项，请更新你的 TodoWrite。</reminder>",
            })

        # 将工具结果注入对话历史
        messages.append({"role": "user", "content": tool_results})

        # ── 手动压缩（LLM 请求的 compress 工具）─────────────
        if manual_compress:
            on_event(Event(type=EventType.COMPACT, compact_reason="manual"))
            messages[:] = auto_compact(messages)
            import paulo.main as _pm
            if _pm._renderer: _pm._renderer.end_loop()
            return


# ╔══════════════════════════════════════════════════════════════╗
# ║                    REPL 交互循环                             ║
# ╚══════════════════════════════════════════════════════════════╝

def main():
    """
    paulo CLI 入口函数。

    由 pyproject.toml 的 console_scripts 注册为 'paulo' 命令，
    pip install -e . 后即可在任意终端输入 paulo 启动。
    """
    global agent_mode, _active_plan_id

    # Windows 终端默认 GBK，强制 UTF-8。管道/重定向时跳过
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (io.UnsupportedOperation, AttributeError):
            pass

    # 创建渲染器
    import paulo.main as _pm
    from .core.renderer.rich_renderer import RichRenderer
    _pm._renderer = RichRenderer()
    TEAM.set_event_callback(_pm._renderer.handle)

    # 用 dict 包装模式状态，让 Repl 可以跨模块修改
    from .command.commander import Commander
    mode_ref = {"mode": agent_mode}
    plan_ref = {"id": _active_plan_id}

    repl = Commander(
        history=[],
        agent_loop_fn=agent_loop,
        plans=PLANS,
        task_mgr=TASK_MGR,
        team=TEAM,
        bus=BUS,
        memory_mgr=MEMORY,
        agent_mode_ref=mode_ref,
        active_plan_id_ref=plan_ref,
    )
    repl.run()


if __name__ == "__main__":
    main()

