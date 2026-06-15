"""
错误分类 + 重试策略。

三类错误:
  transient   — 临时故障，退避重试
  tool_error  — 工具执行失败，错误消息交给 LLM
  fatal       — 不可恢复，终止 agent_loop
"""

import time
from ..config import console

# 需要重试的 HTTP 状态码和异常
_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY = 2  # 秒


def is_retryable(exc: Exception) -> bool:
    """判断是否应该重试这个异常。"""
    status = getattr(exc, "status_code", None)
    if status in _RETRYABLE_CODES:
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in ("timeout", "rate limit", "too many requests", "server error"))


def retry(func, *args, **kwargs):
    """
    调用 func(*args, **kwargs)，对 transient 错误最多重试 3 次。

    Returns:
        func 的返回值，或最后一次重试时抛出异常。
    """
    last_exc = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if not is_retryable(e) or attempt == _MAX_RETRIES:
                raise
            delay = _BASE_DELAY ** attempt
            console.print(f"  [dim]retry {attempt}/{_MAX_RETRIES} in {delay}s...[/dim]")
            time.sleep(delay)
    raise last_exc  # 理论上到不了这里
