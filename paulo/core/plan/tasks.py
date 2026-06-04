"""
tasks.py —— 两层任务管理系统（pydantic 数据模型驱动）

第一层：TodoManager（内存会话级）
  - 轻量级，跟随 REPL 会话生命周期
  - 受 Claude Code 的 TodoWrite 启发
  - 数据模型: TodoItem + TodoList（models/todo.py）
  - 校验规则收敛在 pydantic 模型层，Manager 不再手写 if-else

第二层：TaskManager（文件持久级）
  - 重量级，任务以 JSON 文件持久化到 .tasks/ 目录
  - 数据模型: Task（models/task.py）
  - pydantic 接管 JSON 序列化/反序列化/类型校验
  - 支持依赖关系（blocked_by）、所有权（owner）、状态流转
  - 为多 agent（队友）场景设计：队友可 claim 任务，完成后自动解锁依赖者
"""

from ...models import TodoItem, TodoList, Task
from ...config import TASKS_DIR


# ╔══════════════════════════════════════════════════════════════╗
# ║                      TodoManager                            ║
# ╚══════════════════════════════════════════════════════════════╝

class TodoManager:
    """
    内存中的轻量待办清单。

    管理逻辑与校验逻辑完全分离：
    - 校验（格式、数量、状态）→ pydantic 模型层（TodoItem / TodoList）
    - 管理（渲染、nag 提醒、状态查询）→ 本类

    每次 update() 全量替换列表，不是增量操作。
    """

    def __init__(self):
        self.items: list[TodoItem] = []

    def update(self, items: list[dict]) -> str:
        """
        全量更新待办清单。

        pydantic TodoList 自动完成所有校验：
        - content 不能为空（min_length=1）
        - status 只能是 pending/in_progress/completed（Literal）
        - activeForm 不能为空（min_length=1）
        - 最多 20 项（max_length=20）
        - 最多 1 个 in_progress（model_validator）

        Args:
            items: LLM 生成的待办条目列表（dict 格式，来自 Anthropic tool_use）

        Returns:
            格式化后的待办清单渲染字符串

        Raises:
            pydantic.ValidationError: 校验失败时自动抛出，由 TOOL_HANDLERS 捕获
        """
        # 一行完成校验 + 转换：dict → TodoItem → 校验
        todo_list = TodoList(items=items)
        self.items = todo_list.items
        return self.render()

    def render(self) -> str:
        """
        将当前待办清单渲染为可读文本。

        活跃条目显示 " ← {activeForm}" 后缀（Claude Code 的 activeForm 机制），
        帮助 LLM 记住当前正在做什么。
        """
        if not self.items:
            return "无待办项。"

        status_marks = {
            "completed":   "[x]",
            "in_progress": "[>]",
            "pending":     "[ ]",
        }

        lines = []
        for item in self.items:
            mark = status_marks.get(item.status, "[?]")
            # 活跃项附加进行时标签
            suffix = (
                f" <- {item.active_form}" if item.status == "in_progress" else ""
            )
            lines.append(f"{mark} {item.content}{suffix}")

        done_count = sum(1 for item in self.items if item.status == "completed")
        lines.append(f"\n({done_count}/{len(self.items)} 已完成)")
        return "\n".join(lines)

    def has_open_items(self) -> bool:
        """
        是否有未完成的条目。

        用于 nag 提醒：连续多轮未更新 todo 时触发提醒。
        """
        return any(item.status != "completed" for item in self.items)


# ╔══════════════════════════════════════════════════════════════╗
# ║                      TaskManager                            ║
# ╚══════════════════════════════════════════════════════════════╝

class TaskManager:
    """
    文件持久化的任务管理器。

    每个任务存储为一个 JSON 文件: .tasks/task_{id}.json
    使用 Task pydantic 模型进行序列化和反序列化：
    - 保存: task.model_dump_json(indent=2, by_alias=True)
    - 读取: Task.model_validate_json(file.read_text())

    by_alias=True 确保 JSON 中使用 camelCase（blockedBy），
    与 Anthropic API 风格保持一致。
    """

    def __init__(self):
        # 首次运行时自动创建任务目录
        TASKS_DIR.mkdir(parents=True, exist_ok=True)

    def _next_id(self) -> int:
        """自增生成下一个任务 ID"""
        existing_ids = []
        for file in TASKS_DIR.glob("task_*.json"):
            try:
                existing_ids.append(int(file.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return max(existing_ids, default=0) + 1

    def _load(self, task_id: int) -> Task:
        """从文件加载单个任务，pydantic 自动校验"""
        path = TASKS_DIR / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"任务 {task_id} 不存在")
        return Task.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, task: Task):
        """将任务写入 JSON 文件（camelCase 格式）"""
        path = TASKS_DIR / f"task_{task.id}.json"
        path.write_text(
            task.model_dump_json(indent=2, by_alias=True),
            encoding="utf-8",
        )

    def create(self, subject: str, description: str = "") -> str:
        """
        创建新任务。

        pydantic 自动校验 subject 不为空（min_length=1），
        name/description/status/owner/blocked_by 均有默认值。
        """
        task = Task(
            id=self._next_id(),
            subject=subject,
            description=description,
        )
        self._save(task)
        return task.model_dump_json(indent=2, by_alias=True)

    def get(self, task_id: int) -> str:
        """按 ID 获取任务详情（pydantic 自动反序列化 + 校验）"""
        return self._load(task_id).model_dump_json(indent=2, by_alias=True)

    def update(self, task_id: int, status: str = None,
               add_blocked_by: list[int] = None,
               remove_blocked_by: list[int] = None) -> str:
        """
        更新任务状态和依赖。

        当状态设为 completed 时，自动触发依赖解锁：
        遍历所有任务，从它们的 blocked_by 中移除本任务 ID。
        当状态设为 deleted 时，直接删除文件。

        Args:
            task_id:           任务 ID
            status:            新状态（pending/in_progress/completed/deleted）
            add_blocked_by:    新增依赖（需要等待完成的任务 ID 列表）
            remove_blocked_by: 移除已有依赖
        """
        task = self._load(task_id)

        if status:
            task.status = status  # pydantic Literal 校验，非法值直接报错

            if status == "completed":
                # 自动解锁依赖者：遍历所有任务，从 blocked_by 中移除本 ID
                for file in TASKS_DIR.glob("task_*.json"):
                    other = Task.model_validate_json(
                        file.read_text(encoding="utf-8")
                    )
                    if task_id in other.blocked_by:
                        other.blocked_by.remove(task_id)
                        self._save(other)

            if status == "deleted":
                (TASKS_DIR / f"task_{task_id}.json").unlink(missing_ok=True)
                return f"任务 {task_id} 已删除"

        if add_blocked_by:
            # 去重合并
            task.blocked_by = list(set(task.blocked_by + add_blocked_by))
        if remove_blocked_by:
            task.blocked_by = [
                x for x in task.blocked_by if x not in remove_blocked_by
            ]

        self._save(task)
        return task.model_dump_json(indent=2, by_alias=True)

    def list_all(self) -> str:
        """列出所有任务及状态（按 ID 排序）"""
        task_files = sorted(TASKS_DIR.glob("task_*.json"))
        if not task_files:
            return "无任务。"

        status_marks = {
            "pending":     "[ ]",
            "in_progress": "[>]",
            "completed":   "[x]",
        }

        lines = []
        for file in task_files:
            task = Task.model_validate_json(file.read_text(encoding="utf-8"))
            mark = status_marks.get(task.status, "[?]")
            owner = f" @{task.owner}" if task.owner else ""
            blocked = (
                f" (阻塞于: {task.blocked_by})" if task.blocked_by else ""
            )
            lines.append(
                f"{mark} #{task.id}: {task.subject}{owner}{blocked}"
            )

        return "\n".join(lines)

    def claim(self, task_id: int, owner: str) -> str:
        """
        认领一个 pending 任务。

        认领时自动将状态改为 in_progress，
        表示有 agent 正在处理此任务。

        Args:
            task_id: 要认领的任务 ID
            owner:   认领者名称（"lead" 或队友名）
        """
        task = self._load(task_id)
        task.owner = owner
        task.status = "in_progress"
        self._save(task)
        return f"已认领任务 #{task_id} ({owner})"
