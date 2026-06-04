"""
skills/loader.py —— 技能加载器

从 .skills/ 目录加载 .md 文件，和 .memory/ 格式一致。
每个技能文件 YAML frontmatter + Markdown 正文。

文件格式：
    ---
    name: git-workflow
    description: Git 工作流指南
    ---
    正文 Markdown

用法：
    skills.load("git-workflow")  →  返回技能正文
    skills.descriptions()        →  返回所有技能的描述列表
"""

import re
from pathlib import Path


class SkillLoader:
    """技能加载器——扫描 .skills/*.md，解析 YAML frontmatter。"""

    def __init__(self, skills_dir: Path):
        self.skills: dict[str, dict] = {}
        if not skills_dir.exists():
            return

        # 扫描所有 .md 文件（不限定 SKILL.md 命名）
        for f in sorted(skills_dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                if not match:
                    continue

                meta: dict[str, str] = {}
                for line in match.group(1).strip().splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()

                name = meta.get("name", f.stem)
                body = match.group(2).strip()
                self.skills[name] = {"meta": meta, "body": body}
            except Exception:
                continue

    def descriptions(self) -> str:
        """技能摘要列表，注入 System Prompt。"""
        if not self.skills:
            return "(no skills)"
        lines = []
        for name, s in self.skills.items():
            desc = s["meta"].get("description", "-")
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """加载技能正文。"""
        s = self.skills.get(name)
        if not s:
            available = ", ".join(self.skills.keys())
            return f"Error: Unknown skill '{name}'. Available: {available}"
        return f'<skill name="{name}">\n{s["body"]}\n</skill>'
