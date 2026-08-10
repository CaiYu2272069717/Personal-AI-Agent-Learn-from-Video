"""
MCP (Model Context Protocol) 客户端模块

支持通过 stdio 方式连接 MCP 服务器，
动态发现并调用远程工具、读取资源。
"""

import json
import asyncio
import logging
import os
import subprocess
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """MCP 远程工具的描述信息"""
    name: str
    description: str
    input_schema: dict  # JSON Schema
    server_name: str  # 来自哪个 MCP 服务器

    @property
    def full_name(self) -> str:
        """带服务器前缀的完整工具名，如 mcp__server__tool_name"""
        return f"mcp__{self.server_name}__{self.name}"


@dataclass
class MCPServerConfig:
    """单个 MCP 服务器的配置"""
    name: str
    command: str  # 启动命令，如 "npx"
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    transport: str = "stdio"  # "stdio" 或 "sse"
    url: Optional[str] = None  # SSE 模式下的 URL


class MCPConnection:
    """
    与单个 MCP 服务器的连接（stdio 模式）

    通过 JSON-RPC 2.0 协议通信，支持：
    - initialize: 初始化握手
    - tools/list: 获取工具列表
    - tools/call: 调用工具
    - resources/list: 列出资源
    - resources/read: 读取资源
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._tools: list = []
        self._initialized = False
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """启动 MCP 服务器进程并完成初始化握手"""
        try:
            # 合并环境变量
            env = os.environ.copy()
            env.update(self.config.env)

            # 启动子进程
            self.process = subprocess.Popen(
                [self.config.command] + self.config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )

            # 发送 initialize 请求
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "learn-agent",
                    "version": "1.0.0"
                }
            })

            if init_result is None:
                logger.error(f"MCP 服务器 {self.config.name} 初始化失败")
                return False

            # 发送 initialized 通知
            await self._send_notification("notifications/initialized", {})

            # 获取工具列表
            await self._fetch_tools()

            self._initialized = True
            logger.info(
                f"MCP 服务器 {self.config.name} 连接成功，"
                f"发现 {len(self._tools)} 个工具"
            )
            return True

        except Exception as e:
            logger.error(f"连接 MCP 服务器 {self.config.name} 失败: {e}")
            await self.disconnect()
            return False

    async def disconnect(self):
        """断开连接，终止子进程"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.process = None
        self._initialized = False

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """
        调用 MCP 服务器上的工具

        Args:
            tool_name: 工具名（不带 mcp__ 前缀）
            arguments: 工具参数

        Returns:
            工具调用结果
        """
        if not self._initialized:
            raise RuntimeError(f"MCP 服务器 {self.config.name} 未连接")

        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        if result is None:
            return {"error": f"调用工具 {tool_name} 失败"}

        # 解析 MCP 工具返回的 content 格式
        if "content" in result:
            contents = result["content"]
            texts = [c["text"] for c in contents if c.get("type") == "text"]
            return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)

        return result

    async def list_resources(self) -> list:
        """列出 MCP 服务器提供的资源"""
        if not self._initialized:
            return []
        result = await self._send_request("resources/list", {})
        return result.get("resources", []) if result else []

    async def read_resource(self, uri: str) -> Optional[str]:
        """读取指定 URI 的资源内容"""
        if not self._initialized:
            return None
        result = await self._send_request("resources/read", {"uri": uri})
        if result and "contents" in result:
            contents = result["contents"]
            if contents:
                return contents[0].get("text", "")
        return None

    @property
    def tools(self) -> list:
        """获取该服务器提供的所有工具"""
        return self._tools

    async def _fetch_tools(self):
        """从服务器获取工具列表"""
        result = await self._send_request("tools/list", {})
        if result and "tools" in result:
            self._tools = [
                MCPTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    server_name=self.config.name
                )
                for t in result["tools"]
            ]

    async def _send_request(self, method: str, params: dict) -> Optional[dict]:
        """发送 JSON-RPC 请求并等待响应"""
        async with self._lock:
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params
            }
            return await self._communicate(request)

    async def _send_notification(self, method: str, params: dict):
        """发送 JSON-RPC 通知（不需要响应）"""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        await self._write_message(notification)

    async def _communicate(self, request: dict) -> Optional[dict]:
        """写入请求并读取响应"""
        if not self.process or not self.process.stdin or not self.process.stdout:
            return None

        try:
            await self._write_message(request)
            response = await self._read_message()

            if response and "error" in response:
                logger.error(
                    f"MCP RPC 错误 [{self.config.name}]: "
                    f"{response['error']}"
                )
                return None

            return response.get("result") if response else None

        except Exception as e:
            logger.error(f"MCP 通信错误 [{self.config.name}]: {e}")
            return None

    async def _write_message(self, message: dict):
        """写入一条 JSON-RPC 消息（Content-Length 头 + JSON 体）"""
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        self.process.stdin.write(header + body)
        self.process.stdin.flush()

    async def _read_message(self) -> Optional[dict]:
        """读取一条 JSON-RPC 响应消息"""
        try:
            # 读取 Content-Length 头
            header_line = b""
            while True:
                byte = self.process.stdout.read(1)
                if not byte:
                    return None
                header_line += byte
                if header_line.endswith(b"\r\n\r\n"):
                    break

            # 解析 Content-Length
            header_str = header_line.decode("utf-8")
            content_length = 0
            for line in header_str.split("\r\n"):
                if line.startswith("Content-Length:"):
                    content_length = int(line.split(":")[1].strip())
                    break

            if content_length == 0:
                return None

            # 读取 JSON 体
            body = self.process.stdout.read(content_length)
            return json.loads(body.decode("utf-8"))

        except Exception as e:
            logger.error(f"读取 MCP 消息失败: {e}")
            return None


class MCPManager:
    """
    MCP 管理器 —— 管理所有 MCP 服务器连接

    负责：
    - 根据配置文件创建并维护多个 MCP 连接
    - 聚合所有服务器的工具列表
    - 路由工具调用到正确的服务器
    """

    def __init__(self):
        self._connections: dict = {}

    async def load_from_config(self, config_path: str) -> int:
        """
        从配置文件加载 MCP 服务器配置并建立连接

        Args:
            config_path: mcp_config.json 文件路径

        Returns:
            成功连接的服务器数量
        """
        if not os.path.exists(config_path):
            logger.info(f"MCP 配置文件不存在: {config_path}，跳过 MCP 加载")
            return 0

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            logger.error(f"加载 MCP 配置文件失败: {e}")
            return 0

        servers = config_data.get("mcpServers", {})
        connected = 0

        for name, server_conf in servers.items():
            config = MCPServerConfig(
                name=name,
                command=server_conf.get("command", ""),
                args=server_conf.get("args", []),
                env=server_conf.get("env", {}),
                transport=server_conf.get("transport", "stdio"),
                url=server_conf.get("url"),
            )

            conn = MCPConnection(config)
            success = await conn.connect()
            if success:
                self._connections[name] = conn
                connected += 1
            else:
                logger.warning(f"MCP 服务器 {name} 连接失败，跳过")

        logger.info(f"MCP 管理器: {connected}/{len(servers)} 个服务器已连接")
        return connected

    def get_all_tools(self) -> list:
        """获取所有已连接服务器的工具列表"""
        tools = []
        for conn in self._connections.values():
            tools.extend(conn.tools)
        return tools

    def get_tools_as_openai_format(self) -> list:
        """
        将 MCP 工具转换为 OpenAI function calling 格式

        Returns:
            适配 OpenAI API 的工具定义列表
        """
        result = []
        for tool in self.get_all_tools():
            result.append({
                "type": "function",
                "function": {
                    "name": tool.full_name,
                    "description": f"[MCP:{tool.server_name}] {tool.description}",
                    "parameters": tool.input_schema
                }
            })
        return result

    async def call_tool(self, full_name: str, arguments: dict) -> Any:
        """
        通过完整工具名调用 MCP 工具

        Args:
            full_name: 完整工具名，格式为 mcp__server__tool_name
            arguments: 工具参数

        Returns:
            工具调用结果
        """
        # 解析 full_name: mcp__<server>__<tool>
        parts = full_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return {"error": f"无效的 MCP 工具名: {full_name}"}

        server_name = parts[1]
        tool_name = parts[2]

        conn = self._connections.get(server_name)
        if not conn:
            return {"error": f"MCP 服务器 {server_name} 未连接"}

        return await conn.call_tool(tool_name, arguments)

    def is_mcp_tool(self, tool_name: str) -> bool:
        """判断一个工具名是否是 MCP 工具"""
        return tool_name.startswith("mcp__")

    async def shutdown(self):
        """关闭所有 MCP 连接"""
        for name, conn in self._connections.items():
            logger.info(f"断开 MCP 服务器: {name}")
            await conn.disconnect()
        self._connections.clear()

    @property
    def connected_servers(self) -> list:
        """已连接的服务器名列表"""
        return list(self._connections.keys())
