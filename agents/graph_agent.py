"""售前咨询 Agent · LangGraph 状态图编排（简历主项目 · 就业版实现）。

用 LangGraph 的 StateGraph 把售前咨询拆成可编排节点：
    intent → retrieve → generate → multimodal
对比 agents/loop_engine.py（自研 ReAct Loop），本文件是「用主流框架」的就业版实现。

为什么用 LangGraph 而非自研 Loop：
- 状态图可视化、节点可独立测试、可加条件分支/循环（human-in-the-loop 也原生支持）
- 招聘方看的是「会用 LangGraph 编排 Agent」，自研 Loop 只适合讲底层原理
"""
import os
import sys
from typing import TypedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import END, StateGraph

from agents.intent_router import route_label
from apps.presale_agent.multimodal import build_multimodal
from core.llm_gateway import chat
from mcp_server.server import call_tool


class AgentState(TypedDict):
    query: str
    session_id: str
    history: list
    intent: str
    contexts: list
    answer: str
    multimodal: dict


def _intent_node(state: AgentState) -> dict:
    return {"intent": route_label(state["query"])}


def _retrieve_node(state: AgentState) -> dict:
    res = call_tool("rag_search", {"query": state["query"], "top_k": 3})
    return {"contexts": res.get("contexts", [])}


def _generate_node(state: AgentState) -> dict:
    ctx = state.get("contexts", [])
    ctx_text = "\n".join(f"- {c['text']}" for c in ctx) if ctx else "（无检索结果）"
    hist = state.get("history", [])
    hist_text = "\n".join(f"{m['role']}: {m['content']}" for m in hist[-4:]) if hist else "（无）"
    prompt = (
        "你是跨境电商售前咨询助手。基于下面知识回答用户问题，用中文、利益点导向、简洁。\n\n"
        f"知识：\n{ctx_text}\n\n对话历史：\n{hist_text}\n\n用户问题：{state['query']}"
    )
    answer = chat([{"role": "user", "content": prompt}], temperature=0.7, max_tokens=600)
    return {"answer": answer}


def _multimodal_node(state: AgentState) -> dict:
    return {"multimodal": build_multimodal(state["query"], state.get("answer", ""))}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("intent", _intent_node)
    g.add_node("retrieve", _retrieve_node)
    g.add_node("generate", _generate_node)
    g.add_node("multimodal", _multimodal_node)
    g.set_entry_point("intent")
    g.add_edge("intent", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "multimodal")
    g.add_edge("multimodal", END)
    return g.compile()


def consult_graph(query: str, session_id: str = "default", history: list = None) -> dict:
    app = build_graph()
    result = app.invoke({
        "query": query,
        "session_id": session_id,
        "history": history or [],
        "intent": "",
        "contexts": [],
        "answer": "",
        "multimodal": {},
    })
    return {
        "intent": result["intent"],
        "answer": result["answer"],
        "contexts": result["contexts"],
        "multimodal": result["multimodal"],
        "engine": "LangGraph",
    }


if __name__ == "__main__":
    r = consult_graph("推荐一款适合运动的降噪耳机")
    print("引擎:", r["engine"])
    print("意图:", r["intent"])
    print("检索块数:", len(r["contexts"]))
    print("回答:", r["answer"])
    print("多模态:", r["multimodal"])
