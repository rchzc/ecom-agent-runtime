"""售前实时咨询 Agent（简历主项目入口）。

主路径：LangGraph 状态图编排（agents/graph_agent.py）。
意图路由 → RAG 检索 → LLM 生成 → 多模态回复。
自研 Loop Engine（agents/loop_engine.py）保留为「原理对比」，通过 engine="self-loop" 调用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.graph_agent import consult_graph
from agents.intent_router import route_label
from agents.loop_engine import run
from apps.presale_agent.multimodal import build_multimodal
from core.memory import ConversationMemory

_memory = ConversationMemory()


def consult(user_query: str, session_id: str = "default", engine: str = "langgraph",
           with_multimodal: bool = True) -> dict:
    history = _memory.history(session_id)

    if engine == "langgraph":
        r = consult_graph(user_query, session_id=session_id, history=history)
        answer = r["answer"]
        multimodal = r["multimodal"] if with_multimodal else {}
        _memory.append(session_id, "user", user_query)
        _memory.append(session_id, "assistant", answer)
        return {
            "intent": r["intent"],
            "answer": answer,
            "tools_called": ["rag_search"],
            "multimodal": multimodal,
            "engine": "LangGraph",
            "contexts": r["contexts"],
        }

    # 自研 Loop 对比路径（仅用于面试讲底层原理）
    intent_label = route_label(user_query)
    loop = run(user_query, session_history=history, max_iterations=4)
    answer = loop["answer"]
    multimodal = build_multimodal(user_query, answer) if with_multimodal else {}
    _memory.append(session_id, "user", user_query)
    _memory.append(session_id, "assistant", answer)
    return {
        "intent": intent_label,
        "answer": answer,
        "tools_called": loop["tools_called"],
        "multimodal": multimodal,
        "engine": "self-loop",
    }


if __name__ == "__main__":
    r = consult("推荐一款降噪耳机，并告诉我它有什么优惠")
    print("引擎:", r["engine"])
    print("意图:", r["intent"])
    print("回答:", r["answer"])
    print("工具:", r["tools_called"])
    print("多模态:", r["multimodal"])
