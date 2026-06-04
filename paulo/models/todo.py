"""
models/todo.py —— 待办清单的 pydantic 数据模型

TodoItem  —— 单个待办条目
TodoList  —— 条目集合（带校验：最多 20 项、最多 1 个 in_progress）

设计要点：
- 使用 alias 实现 Python snake_case ↔ LLM camelCase 的双向转换
- populate_by_name=True 让两种命名都可用（向后兼容）
- model_validator 替代手写 if-else 校验，校验逻辑集中在模型层
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TodoItem(BaseModel):
    """
    单条待办事项。

    LLM 通过 TodoWrite 工具传入的 JSON 使用 camelCase（如 activeForm），
    Python 代码内部使用 snake_case（active_form）。
    alias + populate_by_name 让两种写法都接受，读写双方都舒服。
    """
    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(
        ...,
        min_length=1,
        description="待办内容摘要，不能为空",
        examples=["编写用户登录接口"],
    )
    status: Literal["pending", "in_progress", "completed"] = Field(
        ...,
        description="当前状态：pending=等待, in_progress=进行中, completed=已完成",
    )
    active_form: str = Field(
        ...,
        min_length=1,
        alias="activeForm",   # LLM 端使用 camelCase，此为 Anthropic API 约定
        description="进行时标签，如 '编写 login.py'，仅在 in_progress 时显示",
    )


class TodoList(BaseModel):
    """
    待办清单的容器模型。

    校验规则（与 Claude Code 行为对齐）：
    1. 至少 1 项，最多 20 项 — 防止列表无限膨胀
    2. 同时只能有 1 个 in_progress 项 — 强制专注，避免假并行
    """
    items: list[TodoItem] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="待办条目列表，全量替换模式",
    )

    @model_validator(mode="after")
    def _ensure_single_in_progress(self) -> "TodoList":
        """
        跨字段校验：确保只有一个 in_progress。

        mode="after" 表示在单个字段校验完成后执行，
        此时 self.items 已经是校验过的 TodoItem 列表，可安全遍历。
        """
        in_progress_count = sum(
            1 for item in self.items if item.status == "in_progress"
        )
        if in_progress_count > 1:
            raise ValueError(
                f"同时只能有 1 个 in_progress 条目，当前有 {in_progress_count} 个"
            )
        return self
