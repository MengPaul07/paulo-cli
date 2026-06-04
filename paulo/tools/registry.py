"""
tools_registry.py —— 工具注册中心（Schema 定义 + 处理器工厂）

将原本散落在 main.py 中的 300+ 行工具定义集中管理。
设计原则：
- 工具 Schema（TOOLS）是纯数据，不依赖任何运行时对象，直接 import 即可
- 工具处理器（TOOL_HANDLERS）通过工厂函数 build_handlers() 创建，依赖注入
- 只读工具集（READONLY_TOOLS）从 TOOLS 自动派生，无需手动维护两份列表

依赖关系：
  tools_registry → tools（纯函数：bash/read/write/edit）
  tools_registry → agents（run_subagent / handle_shutdown / handle_plan_review）
  tools_registry ← main（通过 build_handlers() 注入全局实例）
"""

import json
from collections.abc import Callable

from .base import run_bash, run_read, run_write, run_edit


# ╔══════════════════════════════════════════════════════════════╗
# ║              工具 JSON Schema 定义（纯数据）                  ║
# ╚══════════════════════════════════════════════════════════════╝

# Anthropic API 要求的 tools 参数格式
# 每个工具: { name, description, input_schema }
# input_schema 遵循 JSON Schema 规范
TOOLS: list[dict] = [
    # ── 基础文件操作 ──────────────────────────────────
    {
        "name": "bash",
        "description": "在 WORKDIR 下执行 shell 命令。",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "读取文件内容（可指定行数限制）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "创建或覆盖文件。会自动创建父目录。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "在文件中精确替换文本（仅第一次匹配）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },

    # ── 待办清单 ────────────────────────────────────────
    {
        "name": "TodoWrite",
        "description": "全量更新任务跟踪清单。最多 20 项，同时只能 1 个 in_progress。",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {"type": "string"},
                        },
                        "required": ["content", "status", "activeForm"],
                    },
                },
            },
            "required": ["items"],
        },
    },

    # ── 子代理 ──────────────────────────────────────────
    {
        "name": "task",
        "description": "启动子代理进行隔离探索或工作。Explore=只读，其他可读写。",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "agent_type": {
                    "type": "string",
                    "enum": ["Explore", "general-purpose"],
                },
            },
            "required": ["prompt"],
        },
    },

    # ── 技能加载 ────────────────────────────────────────
    {
        "name": "load_skill",
        "description": "按名称加载专业技能知识。",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },

    # ── 对话压缩 ────────────────────────────────────────
    {
        "name": "compress",
        "description": "手动触发对话上下文压缩。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },

    # ── 后台任务 ────────────────────────────────────────
    {
        "name": "background_run",
        "description": "在后台线程中异步执行命令。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "check_background",
        "description": "检查后台任务状态。",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
        },
    },

    # ── 持久化任务管理 ──────────────────────────────────
    {
        "name": "task_create",
        "description": "创建一个持久化任务（文件存储到 .tasks/）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "task_get",
        "description": "按 ID 获取任务详情。",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "task_update",
        "description": "更新任务状态或依赖关系。",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "remove_blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "task_list",
        "description": "列出所有持久化任务及状态。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },

    # ── 队友管理 ────────────────────────────────────────
    {
        "name": "spawn_teammate",
        "description": "启动一个持久化自主队友（后台线程）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "role": {"type": "string"},
                "prompt": {"type": "string"},
            },
            "required": ["name", "role", "prompt"],
        },
    },
    {
        "name": "list_teammates",
        "description": "列出所有队友及其当前状态。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "send_message",
        "description": "向指定队友发送消息（通过文件收件箱）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "content": {"type": "string"},
                "msg_type": {
                    "type": "string",
                    "enum": [
                        "broadcast", "message", "plan_approval_response",
                        "shutdown_request", "shutdown_response",
                    ],
                },
            },
            "required": ["to", "content"],
        },
    },
    {
        "name": "read_inbox",
        "description": "读取并清空 lead（主代理）的收件箱。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "broadcast",
        "description": "向所有活跃队友广播消息。",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },

    # ── 关机与审批 ──────────────────────────────────────
    {
        "name": "shutdown_request",
        "description": "请求指定队友关机。",
        "input_schema": {
            "type": "object",
            "properties": {"teammate": {"type": "string"}},
            "required": ["teammate"],
        },
    },
    {
        "name": "plan_approval",
        "description": "审批或驳回队友提交的计划。",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "feedback": {"type": "string"},
            },
            "required": ["request_id", "approve"],
        },
    },

    # ── 空闲与认领 ──────────────────────────────────────
    {
        "name": "idle",
        "description": "进入空闲等待状态（仅队友使用，lead 返回提示）。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "claim_task",
        "description": "从任务板认领一个 pending 任务。",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },

]


# ╔══════════════════════════════════════════════════════════════╗
# ║              只读工具集（Plan 模式用）                        ║
# ╚══════════════════════════════════════════════════════════════╝

# Plan 模式只允许探索类工具——白名单，新增工具默认不在计划模式可用
_PLAN_TOOL_NAMES = {"bash", "read_file", "task", "load_skill", "compress", "TodoWrite"}

def build_readonly_tools() -> list[dict]:
    """从 TOOLS 派生只读工具集：仅保留探索工具。"""
    return [t for t in TOOLS if t["name"] in _PLAN_TOOL_NAMES]


# ╔══════════════════════════════════════════════════════════════╗
# ║           工具处理器工厂（依赖注入）                           ║
# ╚══════════════════════════════════════════════════════════════╝

def build_handlers(
    *,
    todo,                    # TodoManager 实例
    skills,                  # SkillLoader 实例
    task_mgr,                # TaskManager 实例
    bg,                      # BackgroundManager 实例
    bus,                     # MessageBus 实例
    team,                    # TeammateManager 实例
    run_subagent_fn: Callable,             # agents.run_subagent
    handle_shutdown_fn: Callable,          # agents.handle_shutdown_request
    handle_plan_review_fn: Callable,       # agents.handle_plan_review
) -> dict[str, Callable]:
    """
    构建工具名 → 处理函数的映射表。

    采用依赖注入模式：所有运行时依赖（全局单例、外部函数）通过参数传入，
    不在本模块内 import 这些依赖，避免循环引用。

    Args:
        todo:                  TodoManager — 待办清单
        skills:                SkillLoader — 技能加载器
        task_mgr:              TaskManager — 持久化任务
        bg:                    BackgroundManager — 后台命令
        bus:                   MessageBus — 消息总线
        team:                  TeammateManager — 队友管理
        run_subagent_fn:       agents.run_subagent — 子代理启动函数
        handle_shutdown_fn:    agents.handle_shutdown_request — 关机请求
        handle_plan_review_fn: agents.handle_plan_review — 计划审批

    Returns:
        {"tool_name": handler_fn} 映射表，供 agent_loop 分发工具调用
    """
    return {
        # ── 基础文件/命令工具 ──────────────────────────────
        "bash":             lambda **kw: run_bash(kw["command"]),
        "read_file":        lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file":        lambda **kw: run_edit(
                                kw["path"], kw["old_text"], kw["new_text"],
                            ),

        # ── 待办清单 ──────────────────────────────────────
        "TodoWrite":        lambda **kw: todo.update(kw["items"]),

        # ── 子代理 ────────────────────────────────────────
        "task":             lambda **kw: run_subagent_fn(
                                kw["prompt"],
                                kw.get("agent_type", "Explore"),
                            ),

        # ── 技能加载 ──────────────────────────────────────
        "load_skill":       lambda **kw: skills.load(kw["name"]),

        # ── 对话压缩 ──────────────────────────────────────
        "compress":         lambda **kw: "正在压缩...（由主循环接管执行）",

        # ── 后台任务 ──────────────────────────────────────
        "background_run":   lambda **kw: bg.run(
                                kw["command"], kw.get("timeout", 120),
                            ),
        "check_background": lambda **kw: bg.check(kw.get("task_id")),

        # ── 持久化任务 ────────────────────────────────────
        "task_create":      lambda **kw: task_mgr.create(
                                kw["subject"], kw.get("description", ""),
                            ),
        "task_get":         lambda **kw: task_mgr.get(kw["task_id"]),
        "task_update":      lambda **kw: task_mgr.update(
                                kw["task_id"],
                                kw.get("status"),
                                kw.get("add_blocked_by"),
                                kw.get("remove_blocked_by"),
                            ),
        "task_list":        lambda **kw: task_mgr.list_all(),

        # ── 队友管理 ──────────────────────────────────────
        "spawn_teammate":   lambda **kw: team.spawn(
                                kw["name"], kw["role"], kw["prompt"],
                            ),
        "list_teammates":   lambda **kw: team.list_all(),
        "send_message":     lambda **kw: bus.send(
                                "lead", kw["to"], kw["content"],
                                kw.get("msg_type", "message"),
                            ),
        "read_inbox":       lambda **kw: json.dumps(
                                bus.read_inbox("lead"),
                                indent=2, ensure_ascii=False,
                            ),
        "broadcast":        lambda **kw: bus.broadcast(
                                "lead", kw["content"], team.member_names(),
                            ),

        # ── 关机 / 计划审批 ───────────────────────────────
        "shutdown_request": lambda **kw: handle_shutdown_fn(
                                kw["teammate"], bus,
                            ),
        "plan_approval":    lambda **kw: handle_plan_review_fn(
                                kw["request_id"], kw["approve"],
                                kw.get("feedback", ""), bus,
                            ),

        # ── 空闲 / 认领 ──────────────────────────────────
        "idle":             lambda **kw: "主代理不使用 idle 状态。",
        "claim_task":       lambda **kw: task_mgr.claim(
                                kw["task_id"], "lead",
                            ),
    }
