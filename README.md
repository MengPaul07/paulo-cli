<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/framework-zero-orange" alt="Zero Framework">
</p>

# Paulo CLI

**Multi‑agent coding assistant built from scratch.** No LangChain. No AutoGPT. Just Python, Anthropic SDK, and prompts.

```bash
$ paulo
s_full >> /plan refactor the auth module
[PLAN] >> /approve
[EXEC] >> ...
```

---

##  Features

- **Plan → Execute pipeline** — agent explores read‑only, submits a plan, you approve, it executes
- **Multi‑agent orchestration** — spawn sub‑agents synchronously or persistent teammates asynchronously
- **HITL approval gate** — sensitive file writes trigger an inline terminal prompt (`y`/`a`/`n`)
- **Streaming Markdown** — real‑time token‑by‑token rendering with syntax highlighting
- **Long‑term memory** — auto‑indexed `.paulo/memory/*.md` with type classification
- **MCP ready** — connect external MCP servers, tools appear alongside built‑ins

##  Quick Start

```bash
git clone https://github.com/your/paulo-cli.git
cd paulo-cli
pip install -e .
```

Create `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
# or
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-chat
```

Launch:

```bash
paulo                        # interactive REPL
paulo "explain this project" # one‑shot
```

##  Architecture

```
paulo/
├── tools/        bash, read, write, edit + HITL gate + executor
├── core/
│   ├── plan/     TodoWrite → Plan → approve → execute
│   ├── multi_agent/  sub‑agent + teammate + message bus
│   ├── memory/        type‑classified long‑term memory
│   ├── skills/        loadable expertise modules
│   └── renderer/      event‑driven Rich terminal UI
├── repl/         REPL loop + command registry
└── models/       pydantic data layer
```

##  Commands

| Command | Description |
|---------|-------------|
| `/plan <task>` | Enter plan mode (read‑only → proposal) |
| `/approve` | Approve pending plan → execute |
| `/reject` | Reject plan → back to chat |
| `/memory <...>` | Browse / save / delete memories |
| `/plans` / `/tasks` / `/team` | View state |
| `/help` | List all commands |
| `/clear` | Reset conversation |
| `Ctrl+C` | Interrupt (does not quit REPL) |

##  Testing

```bash
pytest tests/ -v             # 33 unit + 4 integration
pytest -m "not integration"  # unit only (fast)
```
