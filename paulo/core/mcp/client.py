"""
MCP 客户端 —— Paulo 连接外部 MCP Server，将其工具注入到 Paulo 的 TOOLS 列表。

启动时读取 .paulo_mcp.json 配置，对每个 Server 启动子进程，
通过 MCP stdio 协议（JSON-RPC over stdin/stdout）通信。

用法:
    manager = MCPManager()
    manager.connect_all(".paulo_mcp.json")
    external_tools = manager.get_tools()      # Anthropic JSON Schema 格式
    external_handlers = manager.get_handlers() # tool_name → handler
"""

import json
import os
import subprocess
import threading
from queue import Queue

from ...config import console


class MCPServer:
    """单个 MCP Server 连接。"""

    def __init__(self, name: str, command: str, args: list[str], env: dict | None = None):
        self.name = name
        self._command = command
        self._args = args
        self._env = env
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._pending: dict[int, Queue] = {}  # request_id → response queue
        self._tools: list[dict] = []
        self._reader_thread: threading.Thread | None = None

    def start(self) -> bool:
        """启动子进程，完成 MCP 握手。"""
        try:
            self._process = subprocess.Popen(
                [self._command] + self._args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",          # MCP 协议统一 UTF-8，不用系统默认 GBK
                errors="replace",
                env={**os.environ, **(self._env or {})},
            )
            # 启动 reader 线程
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

            # MCP 初始化握手
            init_resp = self._send("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "paulo", "version": "0.1.0"},
            })
            if not init_resp:
                console.print(f"[yellow]MCP {self.name}: 初始化失败[/yellow]")
                return False

            # 获取工具列表
            tools_resp = self._send("tools/list", {})
            if tools_resp and "tools" in tools_resp.get("result", {}):
                self._tools = tools_resp["result"]["tools"]

            console.print(f"[dim]MCP {self.name}: {len(self._tools)} 个工具已加载[/dim]")
            return True
        except Exception as e:
            console.print(f"[yellow]MCP {self.name}: 连接失败 ({e})[/yellow]")
            return False

    def _send(self, method: str, params: dict) -> dict | None:
        """发送 JSON-RPC 请求，同步等待响应。"""
        self._request_id += 1
        rid = self._request_id
        queue: Queue = Queue()
        self._pending[rid] = queue

        msg = json.dumps({
            "jsonrpc": "2.0", "id": rid, "method": method, "params": params,
        })
        try:
            self._process.stdin.write(msg + "\n")
            self._process.stdin.flush()
        except Exception:
            self._pending.pop(rid, None)
            return None

        # 等待响应（5 秒超时）
        try:
            result = queue.get(timeout=10)
            return result
        except Exception:
            return None
        finally:
            self._pending.pop(rid, None)

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 工具，返回文本结果。"""
        resp = self._send("tools/call", {"name": tool_name, "arguments": arguments})
        if not resp:
            return f"MCP {self.name}: 调用 {tool_name} 超时"

        result = resp.get("result", {})
        content = result.get("content", [])
        # MCP 返回格式: [{type: "text", text: "..."}, ...]
        texts = [item.get("text", str(item)) for item in content if item.get("type") == "text"]
        return "\n".join(texts) if texts else json.dumps(content, ensure_ascii=False)

    def _read_loop(self):
        """后台线程，持续读取子进程 stdout 的 JSON-RPC 响应。"""
        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    rid = msg.get("id")
                    if rid and rid in self._pending:
                        self._pending[rid].put(msg)
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass


class MCPManager:
    """管理所有 MCP Server 连接。"""

    def __init__(self):
        self._servers: dict[str, MCPServer] = {}

    def connect_all(self, config_path: str = ".paulo_mcp.json") -> int:
        """读取配置，连接所有 MCP Server。返回成功连接数。"""
        import os
        path = os.path.join(os.getcwd(), config_path)
        if not os.path.exists(path):
            return 0

        try:
            config = json.loads(open(path).read())
        except Exception:
            console.print(f"[yellow]MCP 配置解析失败: {path}[/yellow]")
            return 0

        servers = config.get("mcpServers", {})
        connected = 0
        for name, cfg in servers.items():
            srv = MCPServer(
                name=name,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env"),
            )
            if srv.start():
                self._servers[name] = srv
                connected += 1
        return connected

    def get_tools(self) -> list[dict]:
        """返回所有 MCP 工具的 Anthropic JSON Schema 格式。"""
        tools = []
        for srv in self._servers.values():
            for tool in srv._tools:
                tools.append({
                    "name": f"mcp_{srv.name}_{tool['name']}",
                    "description": f"[MCP:{srv.name}] {tool.get('description', '')}",
                    "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                })
        return tools

    def get_handlers(self) -> dict:
        """返回 tool_name → handler 映射。"""
        handlers = {}
        for srv in self._servers.values():
            for tool in srv._tools:
                full_name = f"mcp_{srv.name}_{tool['name']}"
                # 闭包捕获 srv 和 tool
                handlers[full_name] = _make_handler(srv, tool["name"])
        return handlers


def _make_handler(srv: MCPServer, tool_name: str):
    """创建 MCP 工具的处理函数。"""
    def handler(**kwargs):
        return srv.call_tool(tool_name, kwargs)
    return handler
