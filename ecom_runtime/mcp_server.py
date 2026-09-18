"""MCP 服务入口：把底座注册的工具用标准 MCP 暴露出去。

**这个文件只有几十行，是刻意为之。** 协议分帧、JSON-RPC 处理、stdio/HTTP 两种传输
全部在共享集群里实现好了，本仓负责的只有"把哪些工具暴露出去"这一个业务决策。

对比一下常见的写法：每个项目自己实现一遍 JSON-RPC 解析 + 传输层，结果是
"本地 stdio 能跑、服务化就挂"（日志写进了 stdout、或两边的错误码不一致）。
这里两种传输共用同一个 `registry.handle()`，行为必然一致。

用法：

    python -m ecom_runtime.mcp_server --selfcheck --offline   # 离线协议自检
    python -m ecom_runtime.mcp_server --transport stdio       # 给本地客户端
    python -m ecom_runtime.mcp_server --transport http        # 服务化，默认只绑 127.0.0.1
"""
from __future__ import annotations

import argparse
import asyncio
import json

from ecom_shared.mcp import serve_http, serve_stdio

from .config import enable_offline
from .runtime import AgentRuntime

SERVER_NAME = "ecom-agent-runtime"


def build_runtime(*, persist_traces: bool = False, with_presale: bool = True) -> AgentRuntime:
    """组装一个装好售前 Agent 的底座 —— MCP 服务、CLI 演示、测试共用这一份。

    装配点只有这里一处：脚本、服务、测试都调它，就不会出现
    "演示脚本挂了多模态节点而 MCP 服务忘了挂"这类不一致。
    """
    from .agents import PresaleAgent, catalog

    runtime = AgentRuntime.build(
        catalog=catalog,
        server_name=SERVER_NAME,
        persist_traces=persist_traces,
    )
    if with_presale:
        PresaleAgent.install(runtime)
    return runtime


async def _selfcheck() -> None:
    """不依赖任何 Key 的最小自检：走一遍 MCP 的 tools/list 与 tools/call。"""
    runtime = build_runtime()
    listed = await runtime.cluster.tools.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    names = [t["name"] for t in listed["result"]["tools"]]
    print("已暴露工具：", ", ".join(names))

    called = await runtime.cluster.tools.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "product_query", "arguments": {"name": "OpenFit"}},
        }
    )
    text = called["result"]["content"][0]["text"]
    print("product_query 自检：", json.loads(text)["found"])
    assert called["result"]["isError"] is False, called
    print("MCP 自检通过")


def main() -> None:
    parser = argparse.ArgumentParser(description="运行时底座的 MCP 服务")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--selfcheck", action="store_true", help="只跑协议自检后退出")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="离线模式：mock 模型 + lexical 检索，不需要任何 Key（clone 下来就能跑）",
    )
    args = parser.parse_args()

    # 必须在任何 build_runtime() 之前 —— 配置是在组装集群时读取的
    if args.offline:
        enable_offline()

    if args.selfcheck:
        asyncio.run(_selfcheck())
        return

    runtime = build_runtime(persist_traces=True)
    if args.transport == "stdio":
        asyncio.run(serve_stdio(runtime.cluster.tools))
    else:
        # 默认绑 127.0.0.1：这个端点没有鉴权，绑 0.0.0.0 等于把工具暴露到内网
        print(f"MCP HTTP: http://{args.host}:{args.port}/mcp  （GET 可直接看工具清单）")
        serve_http(runtime.cluster.tools, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
