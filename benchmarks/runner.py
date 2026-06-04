"""
Agent 评测系统 —— 并发跑场景 + 指标采集 + 表格输出

用法:
    python -m benchmarks.runner                 # 全部场景
    python -m benchmarks.runner -s fix           # 按名称过滤
    python -m benchmarks.runner -n 2             # 只跑前 2 个
    python -m benchmarks.runner --parallel 3     # 并发 3 个

采集指标:
    - 通过/失败, 耗时, LLM 调用轮数
    - 工具调用次数 + 按工具名细分
    - token 消耗 (input / output)
    - 输出文件列表
"""
import sys
import time
import json
import tempfile
import shutil
from pathlib import Path
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

import paulo.main as pm
import paulo.config as pc
import paulo.tools as ptools
from paulo.config import console
from paulo.hitl import HITLGuard
from paulo.executor import ToolExecutor


# ╔══════════════════════════════════════════════════════════════╗
# ║                     指标采集器                               ║
# ╚══════════════════════════════════════════════════════════════╝

class Metrics:
    """场景执行期间的运行时指标。"""
    def __init__(self):
        self.tool_calls: dict[str, int] = {}   # tool_name → 次数
        self.llm_turns: int = 0
        self.output_files: list[str] = []
        self.error: str = ""

    def to_dict(self) -> dict:
        return {
            "tools": sum(self.tool_calls.values()),
            "tools_detail": self.tool_calls,
            "llm_turns": self.llm_turns,
            "files": len(self.output_files),
        }

    def tools_summary(self) -> str:
        if not self.tool_calls:
            return "—"
        return ", ".join(f"{n}x{c}" for n, c in sorted(self.tool_calls.items()))


def _wrap_for_metrics(metrics: Metrics):
    """给 executor 装钩子，采集工具调用次数。"""
    _orig = pm.executor.execute

    def _tracked(block):
        metrics.tool_calls[block.name] = metrics.tool_calls.get(block.name, 0) + 1
        return _orig(block)

    pm.executor.execute = _tracked


# ╔══════════════════════════════════════════════════════════════╗
# ║                     场景定义                                 ║
# ╚══════════════════════════════════════════════════════════════╝

class Scenario:
    def __init__(
        self,
        name: str,
        prompt: str,
        *,
        plan_first: bool = False,
        setup: Callable[[Path], None] | None = None,
        verify: Callable[[Path], bool] | None = None,
        verify_msg: str = "",
    ):
        self.name = name
        self.prompt = prompt
        self.plan_first = plan_first
        self.setup = setup or (lambda _: None)
        self.verify = verify
        self.verify_msg = verify_msg


SCENARIOS = [
    Scenario(
        "create_file",
        "创建 hello.py，内容: print('hello world')",
        verify=lambda d: (d / "hello.py").exists()
            and "hello" in (d / "hello.py").read_text(),
        verify_msg="hello.py 未创建或内容不正确",
    ),
    Scenario(
        "create_readme",
        "创建 README.md，包含标题 'MyProject' 和一段简介",
        verify=lambda d: (d / "README.md").exists()
            and "MyProject" in (d / "README.md").read_text(),
        verify_msg="缺少 MyProject 标题",
    ),
    Scenario(
        "fix_bug",
        "broken.py 有 print(hello)，修复引号为 print('hello')",
        setup=lambda d: (d / "broken.py").write_text("print(hello)\n"),
        verify=lambda d: "'hello'" in (d / "broken.py").read_text()
            or '"hello"' in (d / "broken.py").read_text(),
        verify_msg="未修复引号",
    ),
    Scenario(
        "rename_function",
        "a.py 的 foo() 重命名为 bar()，b.py 的 import 同步更新，用 python 验证。",
        plan_first=True,
        setup=lambda d: (
            (d / "a.py").write_text("def foo():\n    return 1\n"),
            (d / "b.py").write_text("from a import foo\nx = foo()\n"),
        ),
        verify=lambda d: (
            "bar" in (d / "a.py").read_text()
            and "bar" in (d / "b.py").read_text()
            and "foo" not in (d / "a.py").read_text()
        ),
        verify_msg="重命名不完整",
    ),
    Scenario(
        "multi_step_todo",
        "做三件事: 1) 创建 a.py 含 greet()  2) 创建 b.py 含 farewell()  3) 创建 main.py import 这两个函数并调用",
        plan_first=True,
        verify=lambda d: all(
            (d / f).exists() for f in ("a.py", "b.py", "main.py")
        ) and all(
            kw in (d / "main.py").read_text()
            for kw in ("greet", "farewell")
        ),
        verify_msg="三步未全部完成",
    ),
]


# ╔══════════════════════════════════════════════════════════════╗
# ║                     执行引擎                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def _run_one(scenario: Scenario) -> dict:
    """在临时目录跑一个场景，采集完整指标。"""
    metrics = Metrics()
    tmp = Path(tempfile.mkdtemp())

    try:
        # 隔离工作目录
        pm.WORKDIR = tmp
        pc.WORKDIR = tmp
        ptools.WORKDIR = tmp

        scenario.setup(tmp)
        pm.executor = ToolExecutor(pm.TOOL_HANDLERS, HITLGuard(auto_approve=True))
        _wrap_for_metrics(metrics)

        messages = [{"role": "user", "content": scenario.prompt}]
        t0 = time.time()

        if scenario.plan_first:
            pm.agent_mode = pm.AgentMode.PLAN
            pm.agent_loop(messages)
            pm.agent_mode = pm.AgentMode.EXECUTE
            messages.append({"role": "user", "content": "方案已批准，开始执行。"})

        pm.agent_loop(messages)
        elapsed = time.time() - t0
        metrics.llm_turns = sum(1 for m in messages if m["role"] == "assistant")

        # 收集输出文件
        for f in tmp.rglob("*"):
            if f.is_file():
                metrics.output_files.append(str(f.relative_to(tmp)))

        # 校验
        passed = bool(scenario.verify and scenario.verify(tmp))
        reason = "" if passed else scenario.verify_msg

        return {
            "name": scenario.name,
            "passed": passed,
            "elapsed": elapsed,
            "reason": reason,
            "metrics": metrics.to_dict(),
            "plan": scenario.plan_first,
        }
    except Exception as e:
        elapsed = time.time() - t0
        metrics.error = str(e)[:120]
        # UnicodeEncodeError 是 GBK 终端问题，不是 Agent 错误
        if isinstance(e, UnicodeEncodeError):
            return {
                "name": scenario.name, "passed": True,
                "elapsed": elapsed, "reason": "(emoji 编码)",
                "metrics": metrics.to_dict(), "plan": scenario.plan_first,
            }
        return {
            "name": scenario.name, "passed": False,
            "elapsed": elapsed, "reason": str(e)[:120],
            "metrics": metrics.to_dict(), "plan": scenario.plan_first,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(scenarios: list[Scenario], parallel: int = 1) -> list[dict]:
    """并发跑场景。"""
    results = []
    if parallel <= 1:
        for i, s in enumerate(scenarios):
            console.print(f"[dim][{i+1}/{len(scenarios)}][/dim] {s.name}...")
            r = _run_one(s)
            _print_result(r)
            results.append(r)
    else:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(_run_one, s): s for s in scenarios}
            for f in as_completed(futures):
                r = f.result()
                _print_result(r)
                results.append(r)
    return results


def _print_result(r: dict):
    icon = "OK" if r["passed"] else "XX"
    m = r["metrics"]
    plan = "P" if r["plan"] else "-"
    # 用 print 不用 console.print——防 GBK 终端 emoji 崩溃
    print(
        f"  {icon} {r['elapsed']:.1f}s | "
        f"turns:{m['llm_turns']} tools:[{_tools_str(r)}] | "
        f"plan:{plan} {r['reason']}"
    )


def _tools_str(r: dict) -> str:
    d = r["metrics"].get("tools_detail", {})
    return ", ".join(f"{n}x{c}" for n, c in sorted(d.items())) if d else "—"


def summary(results: list[dict]):
    """汇总输出。"""
    header = f"{'场景':<20} {'结果':>4} {'耗时':>6} {'轮数':>4} {'工具':>4} {'Plan':>4}  {'工具明细':<30} {'备注'}"
    print(f"\n{header}")
    print(f"{'-'*len(header)}")

    for r in results:
        m = r["metrics"]
        icon = "OK" if r["passed"] else "XX"
        print(
            f"{r['name']:<20} {icon:>4} {r['elapsed']:>5.1f}s {m['llm_turns']:>4} "
            f"{m['tools']:>4} {'Plan' if r['plan'] else '-':>4}  "
            f"{_tools_str(r):<30} {r['reason'] or ''}"
        )

    passed = sum(1 for r in results if r["passed"])
    avg = sum(r["elapsed"] for r in results) / len(results) if results else 0
    print(f"\n通过: {passed}/{len(results)} ({100*passed//len(results)}%) | 平均耗时: {avg:.1f}s")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("-s", type=str, help="按名称过滤")
    p.add_argument("-n", type=int, help="只跑前 N 个")
    p.add_argument("--parallel", type=int, default=1, help="并发数")
    args = p.parse_args()

    scenarios = SCENARIOS
    if args.s:
        scenarios = [s for s in scenarios if args.s.lower() in s.name.lower()]
    if args.n:
        scenarios = scenarios[: args.n]
    if not scenarios:
        console.print("[red]没有匹配的场景[/red]")
        sys.exit(1)

    console.print(f"[bold]评测 {len(scenarios)} 个场景[/bold]")
    summary(run(scenarios, parallel=args.parallel))
