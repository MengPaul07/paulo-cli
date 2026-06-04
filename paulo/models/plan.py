"""
models/plan.py —— 计划的 pydantic 数据模型

Plan 是从 plans.py 迁移过来的核心模型。
每个计划经历完整的审批→执行→归档生命周期。

生命周期状态机：
    pending ──/approve──► approved ──执行完成──► executed
       │                      │
       └──/reject──► rejected  │
                               │
                         （执行完成后自动标记为 executed）
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class Plan(BaseModel):
    """
    单个计划的完整数据模型。

    必填字段：id / title / content / status
    可选字段：affected_files / plan_steps / risks（LLM 自由填充）
    时间戳字段自动生成。

    设计考量：
    - content 保存 LLM 原始输出（Markdown），不做结构化解析
    - affected_files / plan_steps / risks 是 LLM 可能的结构化信息，
      不强求（不同模型输出格式差异大），由 PlanManager 在需要时尝试提取
    """
    id: int = Field(
        ...,
        description="自增唯一 ID",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="计划标题（从任务描述截取）",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="LLM 输出的完整计划文本（Markdown 格式）",
    )
    status: Literal["pending", "approved", "rejected", "executed"] = Field(
        default="pending",
        description="计划审批状态",
    )

    # ── 时间戳（ISO 8601 UTC）──────────────────────────────
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="创建时间",
    )
    updated_at: str | None = Field(
        default=None,
        description="最后状态变更时间",
    )

    # ── LLM 可选结构化字段 ─────────────────────────────────
    affected_files: list[str] = Field(
        default_factory=list,
        description="预计影响的文件列表",
    )
    plan_steps: list[str] = Field(
        default_factory=list,
        description="分步执行计划",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="风险点和注意事项",
    )

    # ── 状态变更方法 ──────────────────────────────────────

    def approve(self):
        """批准此计划"""
        self.status = "approved"
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def reject(self):
        """拒绝此计划"""
        self.status = "rejected"
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_executed(self):
        """标记为已执行"""
        self.status = "executed"
        self.updated_at = datetime.now(timezone.utc).isoformat()

    # ── 展示方法 ──────────────────────────────────────────

    def summary(self) -> str:
        """单行摘要，用于 /plans 列表"""
        marks = {
            "pending": "[⏳]", "approved": "[✅]",
            "rejected": "[❌]", "executed": "[✔]",
        }
        mark = marks.get(self.status, "[?]")
        return f"{mark} #{self.id}: {self.title} ({self.created_at[:19]})"

    def detail(self) -> str:
        """完整详情，用于 /plan show <id>"""
        lines = [
            f"{'─' * 50}",
            f"计划 #{self.id}: {self.title}",
            f"状态: {self.status}",
            f"创建: {self.created_at[:19]}",
        ]
        if self.updated_at:
            lines.append(f"更新: {self.updated_at[:19]}")
        if self.affected_files:
            lines.append(f"\n影响文件 ({len(self.affected_files)}):")
            for f in self.affected_files:
                lines.append(f"  - {f}")
        if self.plan_steps:
            lines.append("\n执行步骤:")
            for i, step in enumerate(self.plan_steps, 1):
                lines.append(f"  {i}. {step}")
        if self.risks:
            lines.append("\n风险点:")
            for r in self.risks:
                lines.append(f"  ⚠ {r}")
        lines.append(f"\n{'─' * 50}")
        lines.append(f"完整计划:\n{self.content}")
        lines.append(f"{'─' * 50}")
        return "\n".join(lines)
