"""LLM 参与的集成测试（需要 API 调用和 token 消耗）。

运行方式：
    pytest tests/test_integration.py -v -s           # 全部
    pytest tests/test_integration.py -v -k "simple"  # 只跑简单
    pytest -m "not integration"                       # 跳过集成
"""
import os
import pytest
import tempfile
from pathlib import Path

import paulo.main as pm
import paulo.config as pc
import paulo.tools.base as ptools
import paulo.core.memory.manager as pmm


@pytest.fixture
def temp_workdir(monkeypatch):
    """临时工作目录——mock Path.cwd，所有模块自动隔离。"""
    tmp = Path(tempfile.mkdtemp())

    # 直接改所有模块的 WORKDIR（config 在导入时就已调用 Path.cwd）
    for m in (pm, pc, ptools):
        monkeypatch.setattr(m, "WORKDIR", tmp)
    # memory 目录切到临时位置
    pmm.MEMORY_DIR = tmp / ".memory"
    pm.MEMORY._refresh_index()

    yield tmp
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.integration
class TestSimpleTask:
    """单轮对话——不涉及 plan，只验证 agent 能完成任务。"""

    def test_create_file(self, temp_workdir, monkeypatch):
        """创建 hello.py 并写入内容。"""
        # 跳过审批
        from paulo.tools.hitl import HITLGuard
        from paulo.tools.executor import ToolExecutor
        monkeypatch.setattr(pm, "executor",
                           ToolExecutor(pm.TOOL_HANDLERS, HITLGuard(auto_approve=True)))

        messages = [{"role": "user", "content": "创建 hello.py，内容: print('hello')"}]
        pm.agent_mode = pm.AgentMode.CHAT
        pm.agent_loop(messages)

        # 验证文件存在且内容正确
        f = temp_workdir / "hello.py"
        assert f.exists(), "hello.py 未被创建"
        content = f.read_text()
        assert "hello" in content, f"内容不正确: {content}"

    def test_read_and_fix(self, temp_workdir, monkeypatch):
        """读取有 bug 的文件并修复。"""
        from paulo.tools.hitl import HITLGuard
        from paulo.tools.executor import ToolExecutor
        monkeypatch.setattr(pm, "executor",
                           ToolExecutor(pm.TOOL_HANDLERS, HITLGuard(auto_approve=True)))

        # 准备有 bug 的文件
        (temp_workdir / "broken.py").write_text("print(hello)\n")

        messages = [{
            "role": "user",
            "content": "broken.py 里 print(hello) 缺少引号，修复为 print('hello')"
        }]
        pm.agent_mode = pm.AgentMode.CHAT
        pm.agent_loop(messages)

        content = (temp_workdir / "broken.py").read_text()
        assert "'hello'" in content or '"hello"' in content, f"未修复: {content}"


@pytest.mark.integration
class TestPlanFlow:
    """Plan → Approve → Execute 完整流程。"""

    def test_plan_execute(self, temp_workdir, monkeypatch):
        """计划模式产出方案 → 批准 → 执行 → 验证产物。"""
        from paulo.tools.hitl import HITLGuard
        from paulo.tools.executor import ToolExecutor
        monkeypatch.setattr(pm, "executor",
                           ToolExecutor(pm.TOOL_HANDLERS, HITLGuard(auto_approve=True)))

        # 准备初始文件
        (temp_workdir / "lib.py").write_text("def greet():\n    print('hi')\n")

        # Phase 1: Plan 模式
        messages = [{"role": "user", "content": "给 lib.py 的 greet 函数加 docstring"}]
        pm.agent_mode = pm.AgentMode.PLAN
        pm.agent_loop(messages)

        # 提取方案并保存
        last = messages[-1].get("content")
        plan_text = "\n".join(b.text for b in last if hasattr(b, "text")) if isinstance(last, list) else str(last)
        plan = pm.PLANS.create(title="加 docstring", content=plan_text)
        pm.PLANS.approve(plan.id)

        # Phase 2: Execute 模式
        pm.agent_mode = pm.AgentMode.EXECUTE
        messages.append({
            "role": "user",
            "content": f"方案已批准。请根据方案创建 TodoWrite 并执行：\n\n{plan.content}",
        })
        pm.agent_loop(messages)

        # 验证 docstring 已被添加
        content = (temp_workdir / "lib.py").read_text()
        assert '"""' in content or "'''" in content, f"未添加 docstring: {content}"
        assert "greet" in content


@pytest.mark.integration
class TestMemoryLLM:
    """记忆通过 LLM 存取。"""

    def test_memory_save_and_list(self, temp_workdir, monkeypatch):
        """LLM 调 memory_save 保存记忆。"""
        from paulo.tools.hitl import HITLGuard
        from paulo.tools.executor import ToolExecutor
        monkeypatch.setattr(pm, "executor",
                           ToolExecutor(pm.TOOL_HANDLERS, HITLGuard(auto_approve=True)))

        messages = [{
            "role": "user",
            "content": "请记住：项目使用 pytest 做测试框架。用 memory_save 工具保存，type=user。"
        }]
        pm.agent_mode = pm.AgentMode.CHAT
        pm.agent_loop(messages)

        # 验证记忆已存入
        pm.MEMORY._refresh_index()
        saved = pm.MEMORY.list_all()
        assert "pytest" in saved.lower(), f"记忆未保存: {saved}"
