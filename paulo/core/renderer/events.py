"""
渲染事件定义 —— agent_loop 产出事件，Renderer 消费事件。

所有事件用 dataclass，字段固定，渲染器只读不改。
"""

from dataclasses import dataclass, field
from enum import StrEnum


class EventType(StrEnum):
    """事件类型枚举"""
    # ── LLM 流式 ──────────────────────────
    THINKING        = "thinking"         # LLM 调用开始，等待首 token
    THINKING_DELTA  = "thinking_delta"   # 模型内部思考过程的增量
    THINKING_DONE   = "thinking_done"    # 思考阶段结束
    TEXT_DELTA      = "text_delta"       # 流式文本增量
    TEXT_DONE       = "text_done"        # LLM 本轮回复结束

    # ── 工具调用 ──────────────────────────
    TOOL_CALL   = "tool_call"      # LLM 请求调工具
    TOOL_RESULT = "tool_result"    # 工具执行完成
    HITL_ASK    = "hitl_ask"       # 敏感操作弹审批框
    HITL_RESULT = "hitl_result"    # 审批结果

    # ── 多 Agent ───────────────────────────
    TEAMMATE    = "teammate"       # 队友活动日志

    # ── 系统状态 ──────────────────────────
    COMPACT     = "compact"        # 对话压缩（手动/自动）
    NAG         = "nag"            # TodoWrite 遗漏提醒
    INTERRUPT   = "interrupt"      # 用户中断（Ctrl+C）
    ERROR       = "error"          # 异常


@dataclass
class Event:
    """一个渲染事件。type 必填，其余字段按 type 选填。"""
    type: EventType

    # ── TEXT_DELTA / TEXT_DONE ─────────────
    content: str = ""               # 文本内容

    # ── TOOL_CALL / TOOL_RESULT ────────────
    tool_name: str = ""             # 工具名
    tool_input: dict | None = None  # 工具参数（TOOL_CALL）
    tool_output: str = ""           # 工具结果文本（TOOL_RESULT）
    tool_elapsed: float = 0.0       # 耗时（秒）
    tool_ok: bool = True            # 是否成功

    # ── HITL ───────────────────────────────
    hitl_target: str = ""           # 操作目标（文件路径/命令）
    hitl_preview: str = ""          # 操作预览
    hitl_decision: str = ""         # deny / allow_once / allow_always

    # ── COMPACT ────────────────────────────
    compact_reason: str = ""        # manual / auto

    # ── TEAMMATE ───────────────────────────
    teammate_name: str = ""        # 队友名
    teammate_action: str = ""      # send_message / claim_task / idle / ...

    # ── ERROR ──────────────────────────────
    error_msg: str = ""
