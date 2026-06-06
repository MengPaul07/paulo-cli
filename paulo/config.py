"""
config.py —— 全局配置、环境变量读取、LLM 客户端初始化

所有模块共享的常量、路径和客户端实例都在这里定义。
模块间通过 import 共享同一份实例（Python 模块单例特性）。
"""

import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from rich.console import Console

from .core.renderer.tui import PAULO_THEME

# rich 全局 Console 实例 —— 所有模块统一走这个输出
# 好处：统一的主题、宽度、颜色风格，一处配置全局生效
console = Console(theme=PAULO_THEME, highlight=False)

# ── 加载 .env 文件 ─────────────────────────────────────────────
# override=True: .env 中已存在的变量也会被覆盖（确保 .env 优先生效）
load_dotenv(override=True)

# DeepSeek 等兼容 API 使用 ANTHROPIC_API_KEY 作为 Bearer token，
# 不需要 ANTHROPIC_AUTH_TOKEN（那是 Anthropic 官方的 token 管理服务用的）
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# ── 路径常量 ──────────────────────────────────────────────────
WORKDIR = Path.cwd()
PAULO_DIR = WORKDIR / ".paulo"                # Paulo 数据根目录
PAULO_DIR.mkdir(exist_ok=True)                # 确保根目录存在
TEAM_DIR = PAULO_DIR / "team"
INBOX_DIR = TEAM_DIR / "inbox"
TASKS_DIR = PAULO_DIR / "tasks"
SKILLS_DIR = PAULO_DIR / "skills"
TRANSCRIPT_DIR = PAULO_DIR / "transcripts"

# ── 运行时参数 ────────────────────────────────────────────────
TOKEN_THRESHOLD = 100_000      # 超过此 token 估算值时触发自动压缩
POLL_INTERVAL = 5              # 队友空闲期间轮询收件箱的间隔（秒）
IDLE_TIMEOUT = 60              # 队友空闲超时（秒），超时后自动关机
MAX_SUBAGENT_ROUNDS = 30       # 子代理最大工具调用轮数，防止无限循环

# ── 消息类型白名单（防止非法消息类型）────────────────────────────
VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
}

# ── LLM 客户端初始化 ──────────────────────────────────────────
# 同步实例。异步版在 async_loop.py 内部自建，避免耦合。
client = Anthropic(
    base_url=os.getenv("ANTHROPIC_BASE_URL"),
)
MODEL = os.environ["MODEL_ID"]
