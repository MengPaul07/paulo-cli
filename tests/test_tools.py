"""HITLGuard、ToolExecutor 单元测试。"""
import pytest
from unittest.mock import MagicMock

from paulo.tools.hitl import HITLGuard
from paulo.tools.executor import ToolExecutor


class TestHITLGuard:
    def test_write_is_sensitive(self):
        g = HITLGuard()
        assert g.is_sensitive("write_file", {"path": "a.py"})
        assert g.is_sensitive("edit_file", {"path": "a.py"})

    def test_read_is_not_sensitive(self):
        g = HITLGuard()
        assert not g.is_sensitive("read_file", {"path": "a.py"})
        assert not g.is_sensitive("bash", {"command": "ls"})

    def test_dangerous_bash_is_sensitive(self):
        g = HITLGuard()
        assert g.is_sensitive("bash", {"command": "rm -rf /tmp/"})
        assert g.is_sensitive("bash", {"command": "mv file other"})
        assert g.is_sensitive("bash", {"command": "echo > out.txt"})

    def test_safe_bash_is_not_sensitive(self):
        g = HITLGuard()
        assert not g.is_sensitive("bash", {"command": "ls -la"})
        assert not g.is_sensitive("bash", {"command": "grep foo *.py"})

    def test_auto_approve_skips_all(self):
        g = HITLGuard(auto_approve=True)
        assert not g.is_sensitive("write_file", {"path": "a.py"})
        assert not g.is_sensitive("bash", {"command": "rm -rf /"})

    def test_allowlist(self):
        g = HITLGuard()
        assert not g.is_allowlisted("write_file", {"path": "a.py"})
        g.add_to_allowlist("write_file", {"path": "a.py"})
        assert g.is_allowlisted("write_file", {"path": "a.py"})

    def test_allowlist_different_path(self):
        g = HITLGuard()
        g.add_to_allowlist("write_file", {"path": "a.py"})
        assert not g.is_allowlisted("write_file", {"path": "b.py"})

    def test_ask_deny_default(self, monkeypatch):
        g = HITLGuard()
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert g.ask("write_file", {"path": "a.py", "content": "x"}) == "deny"

    def test_ask_allow_once(self, monkeypatch):
        g = HITLGuard()
        monkeypatch.setattr("builtins.input", lambda _: "y")
        assert g.ask("write_file", {"path": "a.py", "content": "x"}) == "allow_once"

    def test_ask_allow_always(self, monkeypatch):
        g = HITLGuard()
        monkeypatch.setattr("builtins.input", lambda _: "a")
        assert g.ask("write_file", {"path": "a.py", "content": "x"}) == "allow_always"


def make_block(name, input_data):
    """模拟 Anthropic tool_use block。"""
    b = MagicMock()
    b.name = name
    b.type = "tool_use"
    b.id = "tool_001"
    b.input = input_data
    return b


def make_handler(name, return_value):
    """制造一个工具处理函数。"""
    def handler(**kw):
        return return_value
    handler.__name__ = name
    return handler


class TestToolExecutor:
    def test_simple_execute(self):
        handlers = {"bash": make_handler("bash", "ok")}
        executor = ToolExecutor(handlers, HITLGuard())
        block = make_block("bash", {"command": "ls"})
        result = executor.execute(block)
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "tool_001"
        assert result["content"] == "ok"

    def test_unknown_tool(self):
        executor = ToolExecutor({}, HITLGuard())
        block = make_block("no_such_tool", {})
        result = executor.execute(block)
        assert "未知工具" in result["content"]

    def test_sensitive_tool_auto_approve(self):
        g = HITLGuard(auto_approve=True)
        handlers = {"write_file": make_handler("write_file", "done")}
        executor = ToolExecutor(handlers, g)
        block = make_block("write_file", {"path": "a.py", "content": "x"})
        result = executor.execute(block)
        assert result["content"] == "done"

    def test_sensitive_tool_denied(self, monkeypatch):
        g = HITLGuard()
        monkeypatch.setattr("builtins.input", lambda _: "n")
        handlers = {"write_file": make_handler("write_file", "should not run")}
        executor = ToolExecutor(handlers, g)
        block = make_block("write_file", {"path": "a.py", "content": "x"})
        result = executor.execute(block)
        assert "拒绝" in result["content"]
