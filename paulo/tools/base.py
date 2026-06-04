"""
tools.py —— 基础工具实现（bash / read / write / edit）

提供四类最基础的文件系统和命令执行能力。
这些是 LLM agent 与操作系统交互的最小原语集合。

设计原则：
- 路径安全：所有文件操作必须在 WORKDIR 子树内，防止路径穿越攻击
- 输出截断：所有命令输出截断至 50000 字符，防止撑爆 LLM 上下文
- 错误友好：异常不会被传播到 LLM，而是返回友好的错误字符串
- 无状态：所有函数为纯函数（相对工作目录），无副作用全局状态
"""

import subprocess
from pathlib import Path

from ..config import WORKDIR


def safe_path(path_str: str) -> Path:
    """
    将用户输入的路径解析为 WORKDIR 下的绝对路径，并校验安全性。

    防护原理：
    1. 拼接 WORKDIR + 用户路径
    2. resolve() 消除 .. 等符号链接
    3. is_relative_to() 检查结果是否仍在 WORKDIR 下

    这是防止路径穿越（Path Traversal）攻击的关键防线。
    没有这一步，LLM 可以通过 ../../etc/passwd 读取任意系统文件。

    Args:
        path_str: 用户（或 LLM）提供的相对路径

    Returns:
        安全的绝对路径

    Raises:
        ValueError: 路径企图逃逸出工作目录
    """
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"路径越界: {path_str}")
    return path


def run_bash(command: str) -> str:
    """
    在 WORKDIR 下执行 shell 命令。

    安全措施：
    - 危险命令黑名单（rm -rf /、sudo、shutdown、reboot、输出重定向到 /dev/）
    - 120 秒超时，防止死循环撑死进程
    - stdout 和 stderr 合并截断至 50000 字符

    Args:
        command: 要执行的 shell 命令字符串

    Returns:
        命令的标准输出 + 标准错误（截断后），或错误信息
    """
    # 简易危险命令检测 —— 不是完美的沙箱，但对 LLM 辅助场景足够
    dangerous_patterns = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(pattern in command for pattern in dangerous_patterns):
        return "Error: 危险命令被拦截"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(无输出)"
    except subprocess.TimeoutExpired:
        return "Error: 命令超时 (120s)"


def run_read(path_str: str, limit: int = None) -> str:
    """
    读取文件内容（默认全部，可选行数限制）。

    Args:
        path_str: 相对于 WORKDIR 的文件路径
        limit:    最大读取行数，超过则截断并提示剩余行数

    Returns:
        文件内容字符串（截断至 50000 字符），或错误信息
    """
    try:
        lines = safe_path(path_str).read_text(encoding="utf-8").splitlines()
        if limit is not None and limit < len(lines):
            truncated_lines = lines[:limit]
            truncated_lines.append(f"... (还有 {len(lines) - limit} 行未显示)")
            lines = truncated_lines
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path_str: str, content: str) -> str:
    """
    将内容写入文件（覆盖写）。

    会自动创建不存在的父目录，方便 LLM 在深层目录下新建文件。

    Args:
        path_str: 相对于 WORKDIR 的文件路径
        content:  要写入的文本内容

    Returns:
        写入确认信息（字节数）或错误信息
    """
    try:
        file_path = safe_path(path_str)
        file_path.parent.mkdir(parents=True, exist_ok=True)  # 确保父目录存在
        file_path.write_text(content, encoding="utf-8")
        return f"已写入 {len(content)} 字节到 {path_str}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path_str: str, old_text: str, new_text: str) -> str:
    """
    在文件中执行精确文本替换（仅替换第一次出现）。

    这是 LLM 编辑文件的主要方式。
    使用精确字符串匹配而非正则或 diff，降低 LLM 出错的概率。

    Args:
        path_str: 相对于 WORKDIR 的文件路径
        old_text: 要被替换的原始文本（必须精确匹配，包括空白字符）
        new_text: 替换后的新文本

    Returns:
        编辑确认信息，或 "Text not found" 错误
    """
    try:
        file_path = safe_path(path_str)
        content = file_path.read_text(encoding="utf-8")

        if old_text not in content:
            return f"Error: 在 {path_str} 中未找到指定文本"

        # 只替换第一次出现，避免意外修改多处
        file_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"已编辑 {path_str}"
    except Exception as e:
        return f"Error: {e}"
