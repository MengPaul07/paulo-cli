"""
agents.py —— 子代理（Subagent）+ 队友管理器（TeammateManager）+ 关机和计划协议

本文件是多 agent 编排的核心：

1. run_subagent()：同步子代理
   - 在独立上下文中执行任务，完成后返回摘要
   - 受限制的工具集（Explore 类型无写权限）
   - 最多 30 轮工具调用，有自动终止条件

2. TeammateManager：持久化队友（autonomous teammate）
   - 队友是运行在后台线程中的独立 agent
   - 拥有完整的工具集、收件箱消息、任务认领能力
   - 生命周期：working → idle（轮询）→ shutdown（超时或收到关机请求）
   - s11 自动认领：idle 期间扫描未认领任务，自动抢单

3. 关机协议（s10）：
   - 通过 shutdown_request 消息优雅关闭队友
   - request_id 握手确认，防止重复关闭

4. 计划审批（s10）：
   - 队友提交计划 → lead 审批 → approve/reject 回传
"""

import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Queue

from ...config import client, MODEL, WORKDIR, TEAM_DIR, TASKS_DIR, IDLE_TIMEOUT, POLL_INTERVAL, console
from ...tools.base import run_bash, run_read, run_write, run_edit
from .messaging import MessageBus
from ..plan.tasks import TaskManager


# ╔══════════════════════════════════════════════════════════════╗
# ║                   子代理 (Subagent)                          ║
# ╚══════════════════════════════════════════════════════════════╝

def run_subagent(prompt: str, agent_type: str = "Explore") -> str:
    """
    同步执行一个子代理任务，完成后返回最终文本。

    子代理是与主代理隔离的临时 LLM 会话。它拥有受限的工具集，
    在独立的对话上下文中工作，完成后返回摘要。

    工具集差异：
    - Explore：只读（bash + read），用于探索代码库
    - 其他类型：可写（bash + read + write + edit），用于执行具体任务

    为什么限制轮数？
    防止子代理陷入无限的 tool_use 循环（LLM 反复调用工具无法收敛）。

    Args:
        prompt:      子代理的任务描述
        agent_type:  代理类型，Explore 为只读模式，其他可读写

    Returns:
        子代理的最终文本输出，或失败说明
    """
    # ── 子代理工具定义（与主工具集独立）───────────────────────
    sub_tools = [
        {
            "name": "bash",
            "description": "执行 shell 命令。",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "read_file",
            "description": "读取文件内容。",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    ]

    # 非 Explore 类型额外授予写权限
    if agent_type != "Explore":
        sub_tools += [
            {
                "name": "write_file",
                "description": "创建或覆盖文件。",
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
                "description": "在文件中进行精确文本替换。",
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
        ]

    # 工具分发表：将工具名映射到实际处理函数
    sub_handlers = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"]),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    }

    # 子代理的独立消息列表
    sub_msgs = [{"role": "user", "content": prompt}]
    last_response = None

    # 工具调用循环（最多 30 轮）
    for _ in range(30):
        last_response = client.messages.create(
            model=MODEL,
            messages=sub_msgs,
            tools=sub_tools,
            max_tokens=8000,
        )

        # 将助手回复追加到消息历史
        sub_msgs.append({
            "role": "assistant",
            "content": last_response.content,
        })

        # stop_reason 不是 tool_use 意味着 LLM 认为任务完成
        if last_response.stop_reason != "tool_use":
            break

        # 执行本轮所有工具调用
        tool_results = []
        for block in last_response.content:
            if block.type == "tool_use":
                handler = sub_handlers.get(block.name, lambda **kw: "未知工具")
                try:
                    output = str(handler(**block.input))[:50000]
                except Exception as e:
                    output = f"工具执行错误: {e}"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        # 将工具结果追加到消息历史
        sub_msgs.append({"role": "user", "content": tool_results})

    # 提取最终文本回复
    if last_response:
        text_parts = [
            block.text
            for block in last_response.content
            if hasattr(block, "text")
        ]
        return "".join(text_parts) or "(子代理未生成有效输出)"

    return "(子代理启动失败)"


# ╔══════════════════════════════════════════════════════════════╗
# ║                    关机和计划协议                             ║
# ╚══════════════════════════════════════════════════════════════╝

# 全局请求追踪（模块级字典，所有线程共享）
shutdown_requests: dict[str, dict] = {}   # request_id → {target, status}
plan_requests: dict[str, dict] = {}       # request_id → {from, status, content}


def handle_shutdown_request(teammate: str, bus: MessageBus) -> str:
    """
    向指定队友发送关机请求。

    生成唯一的 request_id，用于握手确认。
    关机请求通过 MessageBus 发送到队友的收件箱，
    队友在 idle 轮询或工作循环中检测到后退出。

    Args:
        teammate: 要关闭的队友名称
        bus:      消息总线实例

    Returns:
        确认信息（含 request_id）
    """
    req_id = str(uuid.uuid4())[:8]
    shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    bus.send(
        "lead",
        teammate,
        "请关机。",
        msg_type="shutdown_request",
        extra={"request_id": req_id},
    )
    return f"关机请求 {req_id} 已发送给 '{teammate}'"


def handle_plan_review(request_id: str, approve: bool, feedback: str, bus: MessageBus) -> str:
    """
    审批队友提交的计划。

    Args:
        request_id: 计划请求 ID
        approve:    True=批准, False=驳回
        feedback:   审批意见
        bus:        消息总线实例

    Returns:
        审批结果确认
    """
    req = plan_requests.get(request_id)
    if not req:
        return f"Error: 未知的计划请求 ID '{request_id}'"

    req["status"] = "approved" if approve else "rejected"
    bus.send(
        "lead",
        req["from"],
        feedback,
        msg_type="plan_approval_response",
        extra={
            "request_id": request_id,
            "approve": approve,
            "feedback": feedback,
        },
    )
    return f"计划 {req['status']} ({req['from']})"


# ╔══════════════════════════════════════════════════════════════╗
# ║                   队友管理器 (TeammateManager)                ║
# ╚══════════════════════════════════════════════════════════════╝

class TeammateManager:
    """
    持久化队友管理器 —— 多 agent 系统的核心编排器。

    架构概览：
    ┌─────────────┐   消息总线    ┌─────────────┐
    │   lead      │◄────────────►│  teammate_1  │
    │  (主代理)    │              │  (后台线程)   │
    └─────────────┘              └─────────────┘

    每个队友是一个独立的后台线程，拥有：
    - 独立的 LLM 对话上下文
    - 完整的工具集（bash/read/write/edit/send_message/idle/claim_task）
    - 收件箱：接收来自 lead 和其他队友的消息
    - 自动认领：空闲时扫描任务板并认领未分配任务

    状态机：
        working ──(任务完成/主动idle)──► idle ──(有新消息/有新任务)──► working
                     │                     │
                     ▼                     ▼ (超时/关机请求)
                  shutdown  ◄────────── shutdown

    设计考量：
    - 线程而非进程：轻量，共享内存，适合 Python GIL 场景
    - JSONL 文件通信：无需 IPC/网络，跨线程天然安全
    - s11 自动认领：空闲队友自动抢任务，实现负载均衡
    - 身份重注入：当上下文被压缩时，重新注入身份信息防止遗忘

    潜在问题和简化：
    - Python GIL 限制了 CPU 并行，但 IO 密集型（API 调用）不受影响
    - 线程安全依赖文件系统和 Queue，无锁设计
    - 队友崩溃不会通知 lead（daemon 线程静默退出）
    """

    def __init__(self, bus: MessageBus, task_mgr: TaskManager):
        """
        Args:
            bus:      消息总线（队友之间通信的通道）
            task_mgr: 任务管理器（队友从中认领任务）
        """
        TEAM_DIR.mkdir(exist_ok=True)
        self.bus = bus
        self.task_mgr = task_mgr
        self.config_path = TEAM_DIR / "config.json"
        self.config = self._load_config()
        self.threads: dict[str, threading.Thread] = {}

    def _load_config(self) -> dict:
        """加载团队配置文件，不存在则返回默认配置"""
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        return {"team_name": "default", "members": []}

    def _save_config(self):
        """持久化团队配置到文件"""
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _find_member(self, name: str) -> dict | None:
        """按名称查找团队成员"""
        for member in self.config["members"]:
            if member["name"] == name:
                return member
        return None

    def _set_status(self, name: str, status: str):
        """更新成员状态并持久化"""
        member = self._find_member(name)
        if member:
            member["status"] = status
            self._save_config()

    def spawn(self, name: str, role: str, prompt: str) -> str:
        """
        启动（或重用）一个队友。

        如果同名队友已存在但处于 idle/shutdown 状态，则复用并重激活。
        如果处于 working 状态，拒绝重复启动（防止冲突）。

        Args:
            name:   队友名称（唯一标识）
            role:   角色描述（如 "代码审查员"、"测试工程师"）
            prompt: 初始任务描述

        Returns:
            启动确认信息
        """
        member = self._find_member(name)

        if member:
            # 已存在：检查是否可重用
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' 当前状态为 {member['status']}，无法启动"
            member["status"] = "working"
            member["role"] = role
        else:
            # 新成员：注册到配置
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)

        self._save_config()

        # 在独立线程中启动队友的主循环
        thread = threading.Thread(
            target=self._agent_loop,
            args=(name, role, prompt),
            daemon=True,
        )
        thread.start()
        self.threads[name] = thread

        return f"已启动队友 '{name}' (角色: {role})"

    def _agent_loop(self, name: str, role: str, prompt: str):
        """
        队友的主循环 —— 工作 + 空闲 + 关机。

        每个队友在此方法中运行完整的 agent 生命周期：
        1. 初始化系统提示和消息列表
        2. 进入工作循环：调用 LLM → 执行工具 → 检测 idle
        3. 进入空闲循环：轮询收件箱和任务板
        4. 有消息或新任务则回到工作循环
        5. 超时或收到关机请求则退出
        """
        team_name = self.config["team_name"]

        # 系统提示：告诉队友它是谁、属于哪个团队、放在哪个目录
        system_prompt = (
            f"你是 '{name}'，角色: {role}，团队: {team_name}，"
            f"工作目录: {WORKDIR}。"
            f"完成任务后使用 idle 工具进入等待状态。"
            f"你可以自动认领未分配的任务。"
        )

        messages = [{"role": "user", "content": prompt}]
        tools = self._build_teammate_tools()

        # ── 外层循环：working ↔ idle ↔ shutdown ──────────────
        while True:
            # ========== 工作阶段 ==========
            for _ in range(50):  # 单次工作最多 50 轮工具调用
                # 检查收件箱：处理来自 lead 或其他队友的消息
                inbox = self.bus.read_inbox(name)
                for msg in inbox:
                    # 检测到关机请求：优雅退出
                    if msg.get("type") == "shutdown_request":
                        self._set_status(name, "shutdown")
                        return
                    # 将消息注入对话上下文
                    messages.append({
                        "role": "user",
                        "content": json.dumps(msg, ensure_ascii=False),
                    })

                # 调用 LLM
                try:
                    response = client.messages.create(
                        model=MODEL,
                        system=system_prompt,
                        messages=messages,
                        tools=tools,
                        max_tokens=8000,
                    )
                except Exception:
                    # API 异常：标记关机并退出，避免无限重试
                    self._set_status(name, "shutdown")
                    return

                messages.append({
                    "role": "assistant",
                    "content": response.content,
                })

                # LLM 不再请求工具 = 认为任务完成
                if response.stop_reason != "tool_use":
                    break

                # 执行本轮工具调用
                tool_results = []
                idle_requested = False

                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    # ── 特殊工具：idle（主动进入空闲）────────
                    if block.name == "idle":
                        idle_requested = True
                        output = "进入空闲状态。"
                    # ── 特殊工具：claim_task（认领任务）────────
                    elif block.name == "claim_task":
                        output = self.task_mgr.claim(
                            block.input["task_id"], name
                        )
                    # ── 特殊工具：send_message（发送消息）──────
                    elif block.name == "send_message":
                        output = self.bus.send(
                            name,
                            block.input["to"],
                            block.input["content"],
                        )
                    # ── 基础工具：bash / read / write / edit ───
                    else:
                        dispatch = {
                            "bash": lambda **kw: run_bash(kw["command"]),
                            "read_file": lambda **kw: run_read(kw["path"]),
                            "write_file": lambda **kw: run_write(
                                kw["path"], kw["content"]
                            ),
                            "edit_file": lambda **kw: run_edit(
                                kw["path"], kw["old_text"], kw["new_text"]
                            ),
                        }
                        try:
                            handler = dispatch.get(
                                block.name, lambda **kw: "未知工具"
                            )
                            output = str(handler(**block.input))
                        except Exception as e:
                            output = f"工具执行错误: {e}"

                    # 控制台输出：方便人工观察多 agent 行为
                    console.print(
                        f"  [dim][{name}][/dim] "
                        f"[bold cyan]{block.name}[/bold cyan]: "
                        f"{output[:120]}"
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    })

                messages.append({"role": "user", "content": tool_results})

                # 如果调用了 idle，跳出工作循环进入空闲阶段
                if idle_requested:
                    break

            # ========== 空闲阶段：轮询收件箱和任务板 ==========
            self._set_status(name, "idle")
            resume = False

            poll_cycles = IDLE_TIMEOUT // max(POLL_INTERVAL, 1)
            for _ in range(poll_cycles):
                time.sleep(POLL_INTERVAL)

                # 检查收件箱：有消息则恢复工作
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self._set_status(name, "shutdown")
                            return
                        messages.append({
                            "role": "user",
                            "content": json.dumps(msg, ensure_ascii=False),
                        })
                    resume = True
                    break

                # 扫描未认领任务 → 自动抢单（s11 特性）
                unclaimed = []
                for file in TASKS_DIR.glob("task_*.json"):
                    task = json.loads(file.read_text(encoding="utf-8"))
                    if (
                        task.get("status") == "pending"
                        and not task.get("owner")
                        and not task.get("blockedBy")
                    ):
                        unclaimed.append(task)

                if unclaimed:
                    # 认领第一个符合条件的任务
                    task = unclaimed[0]
                    self.task_mgr.claim(task["id"], name)

                    # 身份重注入：如果上下文被压缩过（消息列表很短），
                    # 重新注入身份信息，确保队友不丢失自我认知
                    if len(messages) <= 3:
                        messages.insert(0, {
                            "role": "user",
                            "content": (
                                f"<identity>你是 '{name}'，角色: {role}，"
                                f"团队: {team_name}。</identity>"
                            ),
                        })
                        messages.insert(1, {
                            "role": "assistant",
                            "content": f"我是 {name}。继续工作。",
                        })

                    # 将认领的任务注入上下文
                    messages.append({
                        "role": "user",
                        "content": (
                            f"<auto-claimed>任务 #{task['id']}: "
                            f"{task['subject']}\n"
                            f"{task.get('description', '')}</auto-claimed>"
                        ),
                    })
                    messages.append({
                        "role": "assistant",
                        "content": f"已认领任务 #{task['id']}。开始处理。",
                    })
                    resume = True
                    break

            # 超时：没有消息也没有任务 → 关机
            if not resume:
                self._set_status(name, "shutdown")
                return

            # 有工作可做 → 回到工作阶段
            self._set_status(name, "working")

    @staticmethod
    def _build_teammate_tools() -> list[dict]:
        """
        构建队友的工具集 JSON Schema。

        队友工具集包含主代理的大部分工具，外加：
        - idle：主动声明工作完成，进入空闲等待
        - send_message：向其他队友或 lead 发送消息
        - claim_task：从任务板认领任务
        """
        return [
            {
                "name": "bash",
                "description": "执行 shell 命令。",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
            {
                "name": "read_file",
                "description": "读取文件内容。",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "创建或覆盖文件。",
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
                "description": "在文件中进行精确文本替换。",
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
            {
                "name": "send_message",
                "description": "向其他队友或 lead 发送消息。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["to", "content"],
                },
            },
            {
                "name": "idle",
                "description": "表示当前工作已完成，进入空闲等待。",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "claim_task",
                "description": "从任务板认领一个任务。",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "integer"}},
                    "required": ["task_id"],
                },
            },
        ]

    def list_all(self) -> str:
        """列出所有队友及状态（用于 /team 命令）"""
        if not self.config["members"]:
            return "无队友。"

        lines = [f"团队: {self.config['team_name']}"]
        for member in self.config["members"]:
            lines.append(f"  {member['name']} ({member['role']}): {member['status']}")
        return "\n".join(lines)

    def member_names(self) -> list[str]:
        """获取所有队友名称列表（用于广播）"""
        return [m["name"] for m in self.config["members"]]
