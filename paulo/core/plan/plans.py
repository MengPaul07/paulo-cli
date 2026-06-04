"""
plans.py —— 计划管理器（PlanManager），基于 pydantic Plan 模型 + 文件持久化

Plan 数据模型已移至 models/plan.py，本文件只保留 PlanManager 的 CRUD 逻辑。

Plan 生命周期状态机：
    pending ──/approve──► approved ──执行完成──► executed
       │                      │
       └──/reject──► rejected  │
                               │
                         （执行完成后自动标记为 executed）

存储：
    每个计划一个 JSON 文件: .plans/plan_{id}.json
    使用 Plan.model_validate_json() 读取、Plan.model_dump_json() 写入。
"""

from pathlib import Path

from ...models import Plan
from ...config import WORKDIR

# 计划存储目录
PLANS_DIR = WORKDIR / ".paulo" / "plans"


class PlanManager:
    """
    计划的 CRUD 管理器，基于文件持久化 + pydantic 模型。

    Plan 模型的校验和序列化全部由 pydantic 接管：
    - 读取: Plan.model_validate_json()    → 自动校验字段类型和约束
    - 写入: plan.model_dump_json()        → 自动序列化 + 缩进格式化
    - 状态: plan.approve() / reject() / mark_executed() → 模型方法

    与 TaskManager 采用相同的设计模式（自增 ID + JSON 文件 + 目录自动创建）。

    用法示例:
        mgr = PlanManager()
        plan = mgr.create("重构 auth 模块", llm_output_text)
        mgr.approve(plan.id)
        mgr.list_all()
    """

    def __init__(self):
        # 首次运行时自动创建存储目录
        PLANS_DIR.mkdir(parents=True, exist_ok=True)

    def _next_id(self) -> int:
        """自增生成下一个计划 ID"""
        existing_ids = []
        for file in PLANS_DIR.glob("plan_*.json"):
            try:
                existing_ids.append(int(file.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return max(existing_ids, default=0) + 1

    def _load(self, plan_id: int) -> Plan:
        """从文件加载单个计划，pydantic 自动校验"""
        path = PLANS_DIR / f"plan_{plan_id}.json"
        if not path.exists():
            raise ValueError(f"计划 {plan_id} 不存在")
        # model_validate_json 一站式完成：读取 → 解析 → 校验 → 返回 Plan 实例
        return Plan.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, plan: Plan):
        """将计划写入 JSON 文件（pydantic 序列化）"""
        path = PLANS_DIR / f"plan_{plan.id}.json"
        path.write_text(
            plan.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def create(self, title: str, content: str) -> Plan:
        """
        创建一个新计划（状态为 pending）。

        Args:
            title:   计划标题（简短描述，截取任务前 40 字符）
            content: LLM 输出的完整计划文本（Markdown）

        Returns:
            创建好的 Plan 实例
        """
        plan = Plan(
            id=self._next_id(),
            title=title,
            content=content,
            status="pending",
        )
        self._save(plan)
        return plan

    def get(self, plan_id: int) -> Plan:
        """按 ID 获取计划详情"""
        return self._load(plan_id)

    def approve(self, plan_id: int) -> Plan:
        """
        批准一个 pending 计划。

        前置条件：计划状态必须为 pending。
        成功后状态变为 approved 并记录时间戳。
        """
        plan = self._load(plan_id)
        if plan.status != "pending":
            raise ValueError(f"计划 {plan_id} 状态为 {plan.status}，无法批准")
        plan.approve()  # Plan 模型方法：设置 status + updated_at
        self._save(plan)
        return plan

    def reject(self, plan_id: int) -> Plan:
        """
        拒绝一个 pending 计划。

        前置条件：计划状态必须为 pending。
        计划不会删除，保留在历史记录中（状态为 rejected）。
        """
        plan = self._load(plan_id)
        if plan.status != "pending":
            raise ValueError(f"计划 {plan_id} 状态为 {plan.status}，无法拒绝")
        plan.reject()
        self._save(plan)
        return plan

    def mark_executed(self, plan_id: int) -> Plan:
        """
        将已批准的计划标记为已执行。

        前置条件：计划状态必须为 approved（不能跳过审批直接执行）。
        """
        plan = self._load(plan_id)
        if plan.status != "approved":
            raise ValueError(
                f"计划 {plan_id} 状态为 {plan.status}，无法标记为已执行"
            )
        plan.mark_executed()
        self._save(plan)
        return plan

    def list_all(self, status: str = None) -> str:
        """
        列出所有计划（可按状态过滤）。

        Args:
            status: 可选的状态过滤（pending/approved/rejected/executed）

        Returns:
            格式化的计划列表字符串，含各状态统计
        """
        plan_files = sorted(PLANS_DIR.glob("plan_*.json"))
        if not plan_files:
            return "暂无计划记录。"

        # 按状态统计
        stats: dict[str, int] = {
            "pending": 0, "approved": 0, "rejected": 0, "executed": 0,
        }
        lines = ["计划列表:"]

        for file in plan_files:
            plan = Plan.model_validate_json(file.read_text(encoding="utf-8"))
            stats[plan.status] = stats.get(plan.status, 0) + 1

            if status and plan.status != status:
                continue
            lines.append(f"  {plan.summary()}")

        lines.append(
            f"\n统计: {stats['executed']} 已执行 | {stats['approved']} 已批准 | "
            f"{stats['pending']} 待审批 | {stats['rejected']} 已拒绝"
        )
        return "\n".join(lines)

    def latest_pending(self) -> Plan | None:
        """
        获取最近一个待审批的计划。

        用于 /approve 和 /reject 命令自动定位最新的待审批计划，
        无需用户手动指定 ID。

        Returns:
            最新的 pending Plan，如果没有则返回 None
        """
        # 按文件名倒序（最新在前）
        plan_files = sorted(PLANS_DIR.glob("plan_*.json"), reverse=True)
        for file in plan_files:
            plan = Plan.model_validate_json(file.read_text(encoding="utf-8"))
            if plan.status == "pending":
                return plan
        return None
