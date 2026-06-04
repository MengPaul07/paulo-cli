"""MemoryManager CRUD、类型分组、向后兼容测试。"""
import pytest
from pathlib import Path

from paulo.core.memory.manager import MemoryManager, MEMORY_DIR


@pytest.fixture(autouse=True)
def clean_memory():
    """每个测试前后清空 .memory 目录。"""
    for f in MEMORY_DIR.glob("*.md"):
        f.unlink()
    yield
    for f in MEMORY_DIR.glob("*.md"):
        f.unlink()


def test_save_and_get():
    m = MemoryManager()
    m.save("test", "用于测试的记忆", "正文内容", mem_type="user")
    result = m.get("user-test")  # save 自动加类型前缀
    assert "正文内容" in result
    assert "用户偏好" in result  # type label


def test_save_with_type():
    m = MemoryManager()
    m.save("t1", "d", "c", mem_type="user")
    m.save("t2", "d", "c", mem_type="feedback")
    m.save("t3", "d", "c", mem_type="project")
    m.save("t4", "d", "c", mem_type="reference")

    desc = m.descriptions()
    assert "用户偏好" in desc
    assert "行为反馈" in desc
    assert "项目背景" in desc
    assert "参考来源" in desc


def test_list_all_grouped():
    m = MemoryManager()
    m.save("user-a", "偏好 A", "c", mem_type="user")
    m.save("user-b", "偏好 B", "c", mem_type="user")
    m.save("proj-x", "项目 X", "c", mem_type="project")

    output = m.list_all()
    assert "[用户偏好]" in output
    assert "[项目背景]" in output
    assert "user-a" in output
    assert "user-b" in output


def test_list_all_filter():
    m = MemoryManager()
    m.save("a", "d", "c", mem_type="user")
    m.save("b", "d", "c", mem_type="project")

    assert "用户偏好" in m.list_all(mem_type="user")
    assert "项目背景" not in m.list_all(mem_type="user")


def test_remove():
    m = MemoryManager()
    m.save("rm-me", "d", "c")
    assert "user-rm-me" in m._index

    m.remove("user-rm-me")
    assert "user-rm-me" not in m._index


def test_backward_compat_no_type():
    """旧文件无 type 字段时默认 reference。"""
    old_file = MEMORY_DIR / "old.md"
    old_file.write_text("---\nname: old\ndescription: 旧记忆\n---\n正文\n", encoding="utf-8")

    m = MemoryManager()
    assert m._index["old"]["type"] == "reference"
    assert "参考来源" in m.descriptions()


def test_empty_memory():
    m = MemoryManager()
    assert m.descriptions() == ""
    assert "暂无记忆" in m.list_all()
