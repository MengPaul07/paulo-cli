"""
messaging.py —— 后台任务管理器 + 消息总线（pydantic 数据模型驱动）

两个子系统：
1. BackgroundManager: 在后台线程执行耗时命令，通过通知队列告知主循环结果
2. MessageBus: 基于文件的异步消息系统，队友/主代理之间通过收件箱（inbox）通信

消息模型使用 models/message.py 的 Message pydantic 类，
替换了原来的手拼 dict + json.dumps 方式：
- 字段名有 IDE 补全和类型检查，不会拼错
- model_dump_json(by_alias=True) 自动处理 from → "from" 的别名转换
- Message.model_validate_json() 确保读取的消息格式正确
"""

import subprocess
import threading
import uuid
from queue import Queue

from ...models import Message
from ...config import WORKDIR, INBOX_DIR, console


# ╔══════════════════════════════════════════════════════════════╗
# ║                    BackgroundManager                         ║
# ╚══════════════════════════════════════════════════════════════╝

class BackgroundManager:
    """
    后台命令执行器。

    用途：当 LLM 调用 background_run 工具时，命令在独立线程中异步执行，
    主循环不会被阻塞。执行完成后将结果放入通知队列，主循环在下一次迭代中消费。

    设计考量：
    - 使用 daemon 线程：主进程退出时自动清理，不会留下僵尸线程
    - 通知队列（Queue）：线程安全的 FIFO，生产者（执行线程）和消费者（主循环）解耦
    - 输出截断至 50000 字符：防止大输出撑爆上下文
    """

    def __init__(self):
        self.tasks: dict[str, dict] = {}     # task_id → {status, command, result}
        self.notifications: Queue = Queue()   # 线程安全的完成通知队列

    def run(self, command: str, timeout: int = 120) -> str:
        """
        启动一个后台命令。

        Args:
            command: 要执行的 shell 命令
            timeout: 命令超时时间（秒），默认 120

        Returns:
            包含任务 ID 的确认信息
        """
        # 生成 8 位短 UUID 作为任务 ID，方便阅读和引用
        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {
            "status": "running",
            "command": command,
            "result": None,
        }

        # daemon=True: 主线程结束时自动销毁，不阻塞进程退出
        thread = threading.Thread(
            target=self._exec,
            args=(task_id, command, timeout),
            daemon=True,
        )
        thread.start()

        return f"后台任务 {task_id} 已启动: {command[:80]}"

    def _exec(self, task_id: str, command: str, timeout: int):
        """
        后台线程的实际执行逻辑。

        执行完成后更新任务状态并将通知推入队列，
        主循环的 drain() 方法会消费这些通知并注入到对话上下文中。
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=WORKDIR,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout + result.stderr).strip()[:50000]
            self.tasks[task_id].update({
                "status": "completed",
                "result": output or "(无输出)",
            })
        except subprocess.TimeoutExpired:
            self.tasks[task_id].update({
                "status": "error",
                "result": f"超时 ({timeout}s)",
            })
        except Exception as e:
            self.tasks[task_id].update({"status": "error", "result": str(e)})

        # 将完成事件推入队列（result 只取前 500 字符，避免通知内容过长）
        self.notifications.put({
            "task_id": task_id,
            "status": self.tasks[task_id]["status"],
            "result": self.tasks[task_id]["result"][:500],
        })

    def check(self, task_id: str = None) -> str:
        """
        检查后台任务状态。

        Args:
            task_id: 指定任务 ID 则返回单个任务状态，否则返回所有任务列表
        """
        if task_id:
            task = self.tasks.get(task_id)
            if not task:
                return f"未知任务: {task_id}"
            return f"[{task['status']}] {task.get('result') or '(运行中)'}"

        if not self.tasks:
            return "无后台任务。"

        lines = []
        for tid, task in self.tasks.items():
            lines.append(f"{tid}: [{task['status']}] {task['command'][:60]}")
        return "\n".join(lines)

    def drain(self) -> list[dict]:
        """
        消费并清空通知队列中所有待处理的通知。

        主循环在每次 LLM 调用前调用此方法，
        将后台任务完成事件注入到对话上下文中。
        """
        notifications = []
        while not self.notifications.empty():
            notifications.append(self.notifications.get_nowait())
        return notifications


# ╔══════════════════════════════════════════════════════════════╗
# ║                       MessageBus                            ║
# ╚══════════════════════════════════════════════════════════════╝

class MessageBus:
    """
    基于文件的异步消息总线（pydantic Message 模型驱动）。

    消息模型（models/message.py）：
    - type:      消息类型（message/broadcast/shutdown_request 等）
    - from_:     发送者名称（Python 内部用 from_，JSON 序列化为 "from"）
    - content:   消息正文
    - timestamp: Unix 时间戳（自动生成）

    设计考量：
    - 文件而非内存：队友运行在独立线程中，文件天然跨线程共享
    - JSONL 格式：每行一条 Message JSON，追加写入 O(1)
    - 读后即清：读取收件箱时清空文件，每条消息只被消费一次
    - 无需 MQ/Redis：零外部依赖，适合单机多 agent 场景
    - alias 机制：Python 内用 from_，JSON 中用 "from"，双方都自然
    """

    def __init__(self):
        # 确保收件箱目录存在（递归创建）
        INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict = None) -> str:
        """
        向指定接收者发送一条消息（pydantic 模型驱动）。

        构造 Message 实例得到类型安全的字段，
        model_dump_json(by_alias=True) 自动将 from_ 序列化为 "from"。

        Args:
            sender:   发送者名称（"lead" 或队友名）
            to:       接收者名称
            content:  消息正文
            msg_type: 消息类型（message/broadcast/shutdown_request 等）
            extra:    额外键值对，会合并到最终 JSON 中

        Returns:
            确认信息字符串
        """
        # 使用 pydantic 模型替代手拼 dict — 字段名不会写错
        msg = Message(
            type=msg_type,
            from_=sender,                # Python 内用 snake_case
            content=content,
        )

        # model_dump(by_alias=True) 将 from_ → "from"
        data = msg.model_dump(by_alias=True)
        if extra:
            data.update(extra)

        # 追加写入 JSONL 文件
        inbox_path = INBOX_DIR / f"{to}.jsonl"
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(
                # 不用 json.dumps，用 pydantic 内置序列化保证一致性
                Message.model_construct(**data).model_dump_json(by_alias=True)
                + "\n"
            )

        return f"已向 {to} 发送 {msg_type}"

    def read_inbox(self, name: str) -> list[dict]:
        """
        读取并清空指定名称的收件箱。

        pydantic Message.model_validate_json() 在读取时自动校验每条消息的格式，
        如果消息被损坏（字段缺失/类型错误）会在解析时立即报错。

        原子性说明：先读取全部内容到内存，再清空文件。
        极端情况下（进程崩溃在清空前）可能丢失消息，
        但本项目定位为开发辅助工具，可接受此简化设计。

        Args:
            name: 收件箱所有者名称

        Returns:
            消息 dict 列表（已清空），如果收件箱不存在则返回空列表
        """
        inbox_path = INBOX_DIR / f"{name}.jsonl"
        if not inbox_path.exists():
            return []

        # 逐行读取并用 pydantic 校验
        messages = []
        for line in inbox_path.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            try:
                msg = Message.model_validate_json(line)
                messages.append(msg.model_dump(by_alias=True))
            except Exception:
                # 损坏的消息跳过并记录警告（实际场景很少发生）
                console.print(f"[yellow][警告][/yellow] 跳过损坏的消息: {line[:80]}...")

        # 读后清空，确保每条消息只被处理一次
        inbox_path.write_text("", encoding="utf-8")

        return messages

    def broadcast(self, sender: str, content: str, names: list[str]) -> str:
        """
        向所有活跃队友广播消息。

        Args:
            sender:  发送者名称
            content: 广播内容
            names:   接收者名称列表（不包括发送者自身）
        """
        count = 0
        for name in names:
            if name != sender:  # 不给自己发
                self.send(sender, name, content, msg_type="broadcast")
                count += 1
        return f"已向 {count} 位队友广播"
