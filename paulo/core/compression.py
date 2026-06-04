"""
compression.py —— 对话压缩系统

LLM 上下文窗口有限，长对话需要压缩。本模块提供两层策略：

第一层：microcompact（微压缩）
  - 在每次 LLM 调用前自动执行
  - 只清理旧的 tool_result，保留最后 3 个
  - 规则：如果 tool_result 内容超过 100 字符，替换为 "[cleared]"
  - 成本：O(n)，几乎无开销

第二层：auto_compact（自动压缩）
  - 当 token 估算超过阈值（默认 100k）时触发
  - 将完整对话保存到 .transcripts/ 目录
  - 调用 LLM 对整个对话进行摘要
  - 用摘要替换全部历史，从头开始
  - 成本：一次额外的 LLM 调用

两种压缩的协同：
  microcompact 在每个 turn 减少 token 消耗，延迟 auto_compact 的触发。
  auto_compact 是最后的兜底，确保永远不会撑爆上下文。
"""

import json
import time
from pathlib import Path

from ..config import client, MODEL, TRANSCRIPT_DIR, TOKEN_THRESHOLD


def estimate_tokens(messages: list) -> int:
    """
    粗略估算消息列表的 token 数量。

    使用字符数 / 4 的经验公式（英语文本 1 token ≈ 4 字符）。
    这是一个保守的下界估计——实际 token 数通常更高。

    为什么不用 tiktoken？因为不同模型（Claude / DeepSeek / GPT）
    tokenizer 不同，精确计数意义不大。这里只需要一个触发压缩的信号。
    """
    serialized = json.dumps(messages, default=str)
    return len(serialized) // 4


def microcompact(messages: list):
    """
    微压缩：清理旧的工具执行结果，只保留最近 3 个。

    原理：旧轮次的 tool_result 通常已不需要（LLM 已经处理过了），
    但保留最近 3 个可以维持一定上下文连贯性。

    副作用：原地修改传入的 messages 列表（mutable mutation）。
    被裁剪的 tool_result 的 content 被替换为 "[cleared]"，
    保留占位但释放 token 空间。

    仅在 tool_result 数量超过 3 个时才执行清理。
    """
    # 收集所有 tool_result 类型的内容片段
    tool_result_parts = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_result_parts.append(part)

    # 最多保留最后 3 个
    if len(tool_result_parts) <= 3:
        return

    # 清理旧的结果：将 content 替换为占位标记
    for part in tool_result_parts[:-3]:
        if isinstance(part.get("content"), str) and len(part["content"]) > 100:
            part["content"] = "[cleared]"


def auto_compact(messages: list) -> list:
    """
    自动压缩：将整个对话历史保存到文件，并用 LLM 生成的摘要替代。

    步骤：
    1. 确保转录目录存在
    2. 将当前消息列表写入 .transcripts/transcript_{timestamp}.jsonl
    3. 取消息列表的最后 80000 字符（≈ 20000 token）作为摘要输入
    4. 调用 LLM 生成连续性摘要
    5. 返回包含摘要的新消息列表（替换原来的全部历史）

    为什么取最后 80000 字符而非全部？
    — 如果消息列表已经很长，全部发给 LLM 做摘要可能超出 token 限制
    — 末尾部分通常包含最近的上下文，最重要

    Args:
        messages: 当前对话历史（将被清空并替换为摘要）

    Returns:
        新的消息列表，第一项包含压缩摘要（直接赋值给 messages[:]）
    """
    # 确保转录目录存在
    TRANSCRIPT_DIR.mkdir(exist_ok=True)

    # 保存完整历史到 JSONL 文件
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w", encoding="utf-8") as file:
        for msg in messages:
            file.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")

    # 取末尾部分进行摘要（避免摘要本身超出 token 限制）
    serialized = json.dumps(messages, default=str)
    snippet = serialized[-80000:]  # 尾部 80000 字符

    # 调用 LLM 生成摘要
    summary_response = client.messages.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": f"请总结以下对话的关键信息，用于后续连续性（中文）：\n{snippet}",
            }
        ],
        max_tokens=2000,
    )
    summary = summary_response.content[0].text

    # 返回压缩后的新消息列表：
    # 只保留一条包含摘要的消息，附上转录文件路径供查阅
    return [
        {
            "role": "user",
            "content": f"[对话已压缩。完整记录: {transcript_path}]\n\n摘要:\n{summary}",
        }
    ]
