"""
models/task.py —— 持久化任务的 pydantic 数据模型

Task 是一个跨会话的、可依赖的、可认领的任务实体。
每个 Task 存储为 .tasks/task_{id}.json 文件。

与 TodoItem 的区别：
- TodoItem 是会话级便签，进程关了就没
- Task 是持久化的项目看板，多 agent 可协作
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Task(BaseModel):
    """
    持久化任务模型。

    字段说明：
    - id:         自增整数 ID，由 TaskManager 分配
    - subject:    任务标题（必填）
    - description: 任务详细描述（可选）
    - status:     状态流转：pending → in_progress → completed（或直接 deleted）
    - owner:      认领者名称（队友名或 "lead"），None 表示未分配
    - blocked_by: 依赖的其他任务 ID 列表，这些任务完成后本任务才能开工

    状态机：
        pending ──claim──► in_progress ──完成──► completed
           │                   │
           └──delete──► deleted   └──delete──► deleted

    依赖自动解锁：
        当 blocked_by 中的任务完成时，TaskManager 自动从列表中移除已完成 ID。
        这个逻辑在 TaskManager.update() 中实现，不在模型层。
    """
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(
        ...,
        description="自增唯一 ID",
    )
    subject: str = Field(
        ...,
        min_length=1,
        description="任务标题",
        examples=["重构 auth 模块的错误处理"],
    )
    description: str = Field(
        default="",
        description="任务的详细描述",
    )
    status: Literal["pending", "in_progress", "completed", "deleted"] = Field(
        default="pending",
        description="任务状态",
    )
    owner: str | None = Field(
        default=None,
        description="认领此任务的 agent 名称",
    )
    blocked_by: list[int] = Field(
        default_factory=list,
        alias="blockedBy",  # LLM 端使用 camelCase
        description="依赖任务 ID 列表：这些任务必须先完成",
    )
