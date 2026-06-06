<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/zero%20frameworks-orange" alt="Zero">
</p>

# Paulo CLI

**Multi-agent coding assistant built from scratch.**
No LangChain. No AutoGPT. Python, Anthropic SDK, Rich.

```bash
$ paulo
> /plan refactor the auth module
> /approve
> ...
```

---

## Features

| Category | Capabilities |
|---|---|
| **Agent** | Plan → Execute pipeline, multi‑agent orchestration, HITL approval gate |
| **UI** | Streaming Markdown, event‑driven Rich renderer, turn separation |
| **Memory** | Type‑classified long‑term memory, auto‑indexed `.paulo/memory/` |
| **MCP** | Connect external MCP servers — tools alongside built‑ins |
| **Benchmark** | LLM‑as‑Judge + deterministic verify, scenario runner with metrics |

---

## Quick Start

```bash
git clone https://github.com/your/paulo-cli.git
cd paulo-cli
pip install -e .
```

Create `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-chat
```

```bash
paulo              # interactive REPL
paulo "explain"    # one‑shot
```

---

## Architecture

```
paulo/
├── main.py                        entry — globals + agent_loop
├── config.py                      env, paths, client, Rich console
│
├── tools/                         工具系统
│   ├── base.py                    bash, read, write, edit
│   ├── registry.py                TOOLS schema + build_handlers()
│   ├── executor.py                ToolExecutor
│   └── hitl.py                    HITLGuard
│
├── core/                          Agent 核心能力
│   ├── plan/
│   │   ├── plans.py               PlanManager (approve/reject/execute)
│   │   └── tasks.py               TodoManager + TaskManager
│   ├── multi_agent/
│   │   ├── agents.py              sub‑agent + TeammateManager
│   │   └── messaging.py           MessageBus + BackgroundManager
│   ├── memory/manager.py          type‑classified memory + auto‑learn
│   ├── skills/loader.py           SkillLoader (.paulo/skills/)
│   ├── renderer/
│   │   ├── events.py              Event types
│   │   ├── rich_renderer.py       Rich terminal renderer
│   │   └── tui.py                 design tokens (colors, padding)
│   ├── compression.py             micro + auto context compaction
│   ├── token_tracker.py           usage + cost estimation
│   └── mcp/client.py              MCP client (connect external servers)
│
├── command/commander.py           REPL loop + /command handlers
│
└── models/                        pydantic data layer
    ├── todo.py, task.py
    ├── message.py, plan.py
```

---

## Commands

| Command | Description |
|---|---|
| `/plan <task>` | Read‑only exploration → proposal |
| `/approve` / `/reject` | Approve or reject pending plan |
| `/plans` / `/tasks` / `/team` | View system state |
| `/memory` | Browse, save, delete memories (LLM‑routed) |
| `/tokens` | Show token usage + cost |
| `/help` | List all commands |
| `/clear` | Reset conversation history |
| `/chatroom` | Toggle teammate message visibility |
| `Ctrl+C` | Interrupt (does not quit REPL) |

---

## Agent Workflow

```
User Input
  │
  ├── /plan <task>
  │     └── PLAN mode (read‑only tools)
  │           └── LLM explores → outputs TodoWrite
  │                 └── User reviews
  │                       ├── /approve → EXEC mode (full tools)
  │                       └── /reject  → CHAT mode
  │
  ├── /memory ...
  │     └── LLM reads/saves/deletes .paulo/memory/ files
  │
  └── default
        └── CHAT mode (full tools)
              └── LLM streams response → tools → response
                    └── 3 turns → auto‑learn preferences
```

## Benchmark

```bash
python -m benchmarks.runner              # all 5 scenarios
python -m benchmarks.runner -s fix       # filter by name
python -m benchmarks.runner --parallel 3 # concurrent
```

Output:

```
场景                  结果   耗时   轮数   工具  Plan  工具明细
───────────────────────────────────────────────────────────────
create_file           OK   4.3s    2    1    -  write_filex1
fix_bug               OK   8.2s    4    3    -  read_filex1 edit_filex1
rename_function       OK  15.0s    6    5  Plan  read_filex2 edit_filex1

通过: 5/5 (100%) | 平均耗时: 9.2s
```

Scenarios are defined in `benchmarks/runner.py` — add a `Scenario` entry to extend.

## Testing

```bash
pytest tests/ -v               # 33 unit + 4 integration
pytest -m "not integration"    # unit only (fast)
```
