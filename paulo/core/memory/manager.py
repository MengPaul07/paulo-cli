"""
memory.py —— 按主题分类的持久记忆系统（Markdown + YAML 前置元数据）

四种主题类型，各自有描述格式规范：
  user      — 用户行为偏好（解释粒度、编码风格、沟通语言）
  feedback  — 行为约束（正面/负面反馈，规则/事实/原因/做法）
  project   — 非代码可推导信息（截止日期、业务背景、合规原因）
  reference — 事实指针（类似 skill，去哪里找什么事实）

记忆文件格式：
  ---
  name: pref-pytest
  type: user
  description: 用户偏好 pytest 做测试框架
  ---
  用户习惯先用 pytest 写测试再写实现代码。

向后兼容：旧文件无 type 字段时默认视为 reference。
"""

import re
from pathlib import Path

from ...config import WORKDIR

MEMORY_DIR = WORKDIR / ".paulo" / "memory"

# 类型 → 分组标题映射
_TYPE_LABELS = {
    "user":      "用户偏好",
    "feedback":  "行为反馈",
    "project":   "项目背景",
    "reference": "参考来源",
}


class MemoryManager:
    """按主题分类的持久记忆管理器。"""

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict] = {}  # name → {type, description, file_path}
        self._refresh_index()

    # ── 索引 ──────────────────────────────────────────────────

    # 索引文件名（LLM 可直接 read_file 查看全部记忆）
    INDEX_FILE = "MEMORY.md"

    def _refresh_index(self):
        """扫描 .memory/，解析 type 字段（缺失时默认 reference）。"""
        self._index.clear()
        if not MEMORY_DIR.exists():
            return

        for md_file in sorted(MEMORY_DIR.glob("*.md")):
            if md_file.name == self.INDEX_FILE:
                continue  # 跳过索引文件本身
            try:
                text = md_file.read_text(encoding="utf-8")
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                if not match:
                    continue

                meta: dict[str, str] = {}
                for line in match.group(1).strip().splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        meta[key.strip()] = value.strip()

                name = meta.get("name", md_file.stem)
                mem_type = meta.get("type", "reference")
                description = meta.get("description", "(无描述)")

                self._index[name] = {
                    "type": mem_type,
                    "description": description,
                    "file_path": md_file,
                }
            except Exception:
                continue

        self._write_index()

    def _write_index(self):
        """根据当前 _index 生成 MEMORY.md —— LLM 可直接 read_file 查看。"""
        if not MEMORY_DIR.exists():
            return
        if not self._index:
            idx_path = MEMORY_DIR / self.INDEX_FILE
            idx_path.write_text("# 记忆索引\n\n(暂无记忆)\n", encoding="utf-8")
            return

        groups: dict[str, list[tuple[str, str]]] = {}
        for name, info in self._index.items():
            t = info.get("type", "reference")
            groups.setdefault(t, []).append((name, info["description"]))

        lines = ["# 记忆索引\n"]
        lines.append(f"共 {len(self._index)} 条记忆。LLM 可用 read_file 读取此文件了解全貌，"
                     f"用 memory_get 查看具体条目。\n")
        for mem_type in ("user", "feedback", "project", "reference"):
            items = groups.get(mem_type)
            if items:
                label = _TYPE_LABELS.get(mem_type, mem_type)
                lines.append(f"## {label}")
                for name, desc in sorted(items):
                    lines.append(f"- **{name}**: {desc}")
                lines.append("")

        (MEMORY_DIR / self.INDEX_FILE).write_text("\n".join(lines), encoding="utf-8")

    # ── 注入 System Prompt ────────────────────────────────────

    def descriptions(self) -> str:
        """按类型分组生成记忆摘要，注入到 System Prompt。"""
        if not self._index:
            return ""

        # 按类型分组
        groups: dict[str, list[str]] = {}
        for name, info in self._index.items():
            t = info.get("type", "reference")
            groups.setdefault(t, []).append(f"    - {name}: {info['description']}")

        if not groups:
            return ""

        lines = ["## 已知记忆"]
        for mem_type in ("user", "feedback", "project", "reference"):
            items = groups.get(mem_type)
            if items:
                label = _TYPE_LABELS.get(mem_type, mem_type)
                lines.append(f"  [{label}]")
                lines.extend(items)
        return "\n".join(lines)

    # ── CRUD ──────────────────────────────────────────────────

    def save(self, name: str, description: str, content: str,
             mem_type: str = "user") -> str:
        """
        保存记忆。同名文件覆盖，不同名的同类型合并到已有文件末尾。
        命名规范: {type}-{slug}.md，如 user-pytest, feedback-no-comments。
        """
        # 确保名称带类型前缀
        if not name.startswith(f"{mem_type}-"):
            name = f"{mem_type}-{name}"

        file_path = MEMORY_DIR / f"{name}.md"

        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"type: {mem_type}\n"
            f"description: {description}\n"
            f"---\n\n"
        )
        file_path.write_text(frontmatter + content.strip(), encoding="utf-8")
        self._refresh_index()

        return f"已保存记忆 '{name}' [{_TYPE_LABELS.get(mem_type, mem_type)}]"

    def get(self, name: str) -> str:
        """读取记忆全文，展示类型标签。"""
        info = self._index.get(name)
        if not info:
            available = ", ".join(self._index.keys()) or "(无)"
            return f"未知记忆 '{name}'。已保存的记忆: {available}"

        text = info["file_path"].read_text(encoding="utf-8")
        match = re.match(r"^---\n.*?\n---\n(.*)", text, re.DOTALL)
        body = match.group(1).strip() if match else text.strip()
        label = _TYPE_LABELS.get(info.get("type", ""), info.get("type", ""))

        return f"[{label}]\n{body}"

    def remove(self, name: str) -> str:
        """删除记忆。"""
        info = self._index.get(name)
        if not info:
            return f"记忆 '{name}' 不存在"
        info["file_path"].unlink()
        self._refresh_index()
        return f"已删除记忆 '{name}'"

    def list_all(self, mem_type: str = "") -> str:
        """列出记忆，可选按类型过滤。按类型分组显示。"""
        if not self._index:
            return "(暂无记忆。使用 /remember <内容> 创建一条)"

        groups: dict[str, list[str]] = {}
        for name, info in self._index.items():
            t = info.get("type", "reference")
            groups.setdefault(t, []).append(f"  - {name}: {info['description']}")

        lines = ["记忆列表:"]
        for t in ("user", "feedback", "project", "reference"):
            if mem_type and t != mem_type:
                continue
            items = groups.get(t)
            if items:
                lines.append(f"  [{_TYPE_LABELS.get(t, t)}]")
                lines.extend(items)

        if mem_type and not groups.get(mem_type):
            return f"(没有 {_TYPE_LABELS.get(mem_type, mem_type)} 类型的记忆)"
        return "\n".join(lines)


# ── 自动学习 ──────────────────────────────────────────────────

LEARN_PROMPT = """从对话中提取值得记住的信息。每条一行，必须用以下格式:

<type>:<描述>

type 只能四选一，严格按以下定义：
  user      — 关于用户本人的偏好/习惯/风格（回复语言、代码风格、工具偏好）
  feedback  — 从对话中总结的经验教训（什么操作会导致问题、注意事项、踩坑记录）
  project   — 项目背景信息（截止日期、业务需求、合规要求、技术栈版本）
  reference — 知识指针（去哪里找什么信息，类似书签）

描述要求具体可检索，不要模糊概括。同一主题合并为一条。
严格按格式输出，每行一条，没有就回复"无"。

正确示例:
  user: 偏好中文回复，代码注释偏详细，使用 pytest 做测试
  feedback: 修改 config.py 前需备份，config 改动会影响所有模块
  project: 项目需兼容 Python 3.11+，不支持更低版本
  reference: API 文档在 docs/api.md，部署流程在 DEPLOY.md

错误示例（不要这样输出）:
  用户喜欢 pytest                    ← 缺 type 前缀
  user: 写代码                        ← 描述太模糊
  coding: 用 pytest                   ← type 不在四选一里"""


def learn_from_session(history: list[dict], memory_mgr: MemoryManager) -> int:
    """LLM 分析对话，自动分类并存入记忆。"""
    import json

    snippet = json.dumps(history, default=str, ensure_ascii=False)[-5000:]
    if len(snippet) < 200:
        return 0

    try:
        from ...config import client, MODEL
        resp = client.messages.create(
            model=MODEL,
            messages=[{"role": "user", "content": f"{LEARN_PROMPT}\n\n对话:\n{snippet}"}],
            max_tokens=200,
        )
        text = " ".join(
            block.text for block in resp.content if hasattr(block, "text")
        )
    except Exception:
        return 0

    added = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line == "无" or ":" not in line:
            continue

        mem_type, _, desc = line.partition(":")
        mem_type, desc = mem_type.strip(), desc.strip()
        if mem_type not in _TYPE_LABELS or len(desc) < 5:
            continue

        name = _slug(desc)
        if not memory_mgr._index.get(name):
            memory_mgr.save(name=name, description=desc,
                           content=desc, mem_type=mem_type)
            added += 1

    if added:
        from ...config import console
        console.print(f"[dim]  从对话中学习了 {added} 条经验[/dim]")
    return added


def _slug(text: str) -> str:
    """文本 → kebab-case 短名，用于记忆文件名。"""
    import re
    slug = re.sub(r"[^\w一-鿿]", "-", text.strip())[:20]
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"learn-{slug}" if slug else f"learn-{abs(hash(text)) % 1000}"
