"""
Token 用量追踪 + 费用估算。

每次 LLM 调用后读 usage 字段累加，支持 Prompt Cache 命中统计。
"""

from ..config import console


# 近似费率（每 1M token 的 USD）
RATES = {
    "input":        3.00,    # 标准输入
    "output":      15.00,    # 标准输出
    "cache_read":   0.30,    # 缓存命中读取
    "cache_write":  3.75,    # 缓存写入
}


class TokenTracker:
    """累计 token 消耗 + 费用估算。"""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0        # 从缓存读取的 token
        self.cache_write = 0       # 写入缓存的 token
        self.calls = 0             # LLM 调用次数

    def add(self, usage):
        """从 Anthropic usage 对象累加统计。"""
        try:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
        except AttributeError:
            pass
        try:
            self.cache_read += usage.cache_read_input_tokens
            self.cache_write += usage.cache_creation_input_tokens
        except AttributeError:
            pass
        self.calls += 1

    def estimate_cost(self) -> float:
        """USD 费用估算（近似）。"""
        cost = 0.0
        regular_input = self.input_tokens - self.cache_read - self.cache_write
        if regular_input > 0:
            cost += regular_input / 1_000_000 * RATES["input"]
        cost += self.output_tokens / 1_000_000 * RATES["output"]
        cost += self.cache_read / 1_000_000 * RATES["cache_read"]
        cost += self.cache_write / 1_000_000 * RATES["cache_write"]
        return cost

    def summary(self) -> str:
        """单行摘要：总 token + 缓存命中 + 费用。"""
        total = self.input_tokens + self.output_tokens
        cost = self.estimate_cost()
        parts = [f"{total:,} tok"]
        if self.cache_read > 0:
            parts.append(f"cache: {self.cache_read:,}")
        parts.append(f"${cost:.4f}")
        return " | ".join(parts)

    def detailed(self) -> str:
        """多行详情。"""
        return (
            f"  LLM 调用: {self.calls} 次\n"
            f"  输入: {self.input_tokens:,} token\n"
            f"  输出: {self.output_tokens:,} token\n"
            f"  缓存命中: {self.cache_read:,} | 写入: {self.cache_write:,}\n"
            f"  估算费用: ${self.estimate_cost():.4f}"
        )


# 全局单例
tracker = TokenTracker()
