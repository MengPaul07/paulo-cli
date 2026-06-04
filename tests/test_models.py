"""pydantic 模型校验、序列化、状态机测试。"""
import json
import pytest
from pydantic import ValidationError

from paulo.models import TodoItem, TodoList, Task, Message, Plan


class TestTodoItem:
    def test_valid_item(self):
        item = TodoItem(content="写测试", status="in_progress", activeForm="编写 test.py")
        assert item.content == "写测试"
        assert item.status == "in_progress"

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            TodoItem(content="", status="pending", activeForm="x")

    def test_bad_status_rejected(self):
        with pytest.raises(ValidationError):
            TodoItem(content="x", status="done", activeForm="x")  # "done" not in enum

    def test_alias_serialization(self):
        item = TodoItem(content="写", status="pending", activeForm="x")
        d = item.model_dump(by_alias=True)
        assert "activeForm" in d  # LLM 侧用 camelCase


class TestTodoList:
    def test_max_20_items(self):
        items = [{"content": f"t{i}", "status": "pending", "activeForm": "x"} for i in range(21)]
        with pytest.raises(ValidationError):
            TodoList(items=items)

    def test_only_one_in_progress(self):
        items = [
            {"content": "a", "status": "in_progress", "activeForm": "做A"},
            {"content": "b", "status": "in_progress", "activeForm": "做B"},
        ]
        with pytest.raises(ValidationError):
            TodoList(items=items)


class TestTask:
    def test_defaults(self):
        t = Task(id=1, subject="测试任务")
        assert t.status == "pending"
        assert t.owner is None
        assert t.blocked_by == []

    def test_camelcase_alias(self):
        t = Task(id=1, subject="s", blocked_by=[2, 3])
        d = json.loads(t.model_dump_json(by_alias=True))
        assert "blockedBy" in d
        assert d["blockedBy"] == [2, 3]


class TestMessage:
    def test_from_alias(self):
        msg = Message(type="message", from_="alice", content="hello")
        d = msg.model_dump(by_alias=True)
        assert d["from"] == "alice"


class TestPlan:
    def test_lifecycle(self):
        p = Plan(id=1, title="测试", content="内容")
        assert p.status == "pending"

        p.approve()
        assert p.status == "approved"
        assert p.updated_at is not None

        p.mark_executed()
        assert p.status == "executed"

    def test_reject(self):
        p = Plan(id=1, title="t", content="c")
        p.reject()
        assert p.status == "rejected"

    def test_summary(self):
        p = Plan(id=1, title="测试计划", content="c")
        s = p.summary()
        assert "测试计划" in s
        assert "#1" in s
