"""MCP & Agent 共享集群（自研实现，接口对齐 MCP 工具协议）。

对外暴露标准化工具，任意 Agent 即插即用：
- rag_search    ：检索知识库（被售前 Agent 调用）
- merchant_query：查询商户/商品信息（mock 数据，真实环境接数据库）
- notify        ：发送通知（飞书/N8N 占位，故障隔离）
- alert         ：智能预警（阈值判断占位）

设计亮点：工具以 name -> {func, schema} 注册；Agent 通过 call_tool(name, args) 调用，
与 MCP 协议「tools/call」语义一致。面试可讲「Function Calling vs MCP」差异：
MCP 把工具做成标准化服务，多 Agent 共享同一套，避免重复实现。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rag_service import RAGService

_rag = RAGService()

MERCHANTS = {
    "星巴克": {"category": "咖啡", "feature": "门店现磨、会员折扣", "promo": "拿铁第二杯半价"},
    "苹果": {"category": "3C", "feature": "官方保修、以旧换新", "promo": "教育优惠季"},
}

TOOLS = {}  # name -> {func, description, parameters}


def _register(name, description, parameters, func):
    TOOLS[name] = {"func": func, "description": description, "parameters": parameters}


def rag_search(query: str, top_k: int = 3, source: str = None) -> dict:
    """检索知识库，返回 top_k 上下文片段（含 source 来源标记）。"""
    results = _rag.search(query, top_k=top_k, source=source)
    return {"count": len(results),
            "contexts": [{"text": r["text"], "score": round(r["score"], 3),
                          "source": r["meta"]["source"]} for r in results]}


def merchant_query(name: str) -> dict:
    info = MERCHANTS.get(name)
    if not info:
        return {"found": False, "name": name}
    return {"found": True, "name": name, **info}


def notify(message: str, channel: str = "feishu") -> dict:
    """发送通知（占位实现，真实环境接飞书/N8N Webhook）。"""
    try:
        print(f"[notify->{channel}] {message}")
        return {"ok": True, "channel": channel}
    except Exception as e:  # 故障隔离：通知失败不影响主链路
        return {"ok": False, "error": str(e)}


def alert(message: str, level: str = "warn") -> dict:
    print(f"[ALERT:{level}] {message}")
    return {"ok": True, "level": level}


_register("rag_search", "检索商品/运营知识库", {"query": "string", "top_k": "int", "source": "string"}, rag_search)
_register("merchant_query", "查询商户信息", {"name": "string"}, merchant_query)
_register("notify", "发送通知到飞书/N8N", {"message": "string", "channel": "string"}, notify)
_register("alert", "触发智能预警", {"message": "string", "level": "string"}, alert)


def list_tools() -> list:
    return [{"name": n, "description": t["description"], "parameters": t["parameters"]} for n, t in TOOLS.items()]


def call_tool(name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool: {name}"}
    if not isinstance(args, dict):
        args = {}
    try:
        return TOOLS[name]["func"](**args)
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    print("可用工具:", [t["name"] for t in list_tools()])
    print(call_tool("merchant_query", {"name": "星巴克"}))
    print(call_tool("rag_search", {"query": "降噪耳机", "top_k": 2}))
