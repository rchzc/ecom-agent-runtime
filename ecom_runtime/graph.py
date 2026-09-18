"""LangGraph 状态图编排：把流程写成显式的节点与边。

    intent → retrieve → generate → [post]

**它和 loop.py 的区别不是"新旧"，而是"谁来决定下一步"：**

- 这里：**流程由代码决定**。检索是固定的一步，一定发生在生成之前。
  好处是可预测、每个节点能单独测、要加分支（比如"知识库没命中就转人工"）
  就多连一条边。线上跑业务链路用这个。
- loop.py：**下一步由模型决定**。模型可能先查商品、再查库存、也可能直接回答。
  好处是处理开放式问题更灵活，代价是路径不可预测。

同一个底座里两条路径并存，是因为这两种需求都真实存在；对外按 `engine=` 选一条即可。
两者产出的都是同一种 `Trace`，所以轨迹库、失败分析、回放对两条路径一视同仁 ——
如果自进化只覆盖其中一条，那它就不是"运行时底座"的能力，只是某个引擎的特性。

Prompt 不写在这里：走共享集群的 `rag_answer` 模板（含"无依据时要说不知道"这类
约束），改话术只改模板，不动编排代码。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict

from ecom_shared import SharedCluster, parse_json_lenient
from langgraph.graph import END, StateGraph

from .config import MAX_ITERATIONS_DEFAULT
from .evolution import Trace, TraceStore, classify_failure
from .intent import DOMAINS, route, search_domain

#: 生成节点之后的额外节点（售前 Agent 用它挂"多模态素材"，别处可以不挂）
PostNode = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentState(TypedDict, total=False):
    query: str
    session_id: str
    history: list[dict[str, str]]
    domain: str
    contexts: list[dict[str, Any]]
    answer: str
    used_sources: list[str]
    need_human: bool
    extra: dict[str, Any]


def build_graph(cluster: SharedCluster, *, post_node: PostNode | None = None):
    """组装状态图。`post_node` 为 None 时流程止于 generate。"""

    async def intent_node(state: AgentState) -> dict[str, Any]:
        # 域判定用规则而不是模型：它是闭集短文本分类，关键词命中率足够，
        # 而它处在链路最前面，一次模型调用会让整条链路多一跳延迟。
        return {"domain": route(state["query"])}

    async def retrieve_node(state: AgentState) -> dict[str, Any]:
        result = await cluster.tools.call(
            "rag_search",
            {
                "query": state["query"],
                # 用 search_domain 而不是直接把 state["domain"] 传下去：
                # general 是路由概念、不是知识库目录，直传会导致零命中。
                "domain": search_domain(state["query"]),
                "top_k": 3,
            },
        )
        if not result.get("ok"):
            # 检索失败不当作致命错误：没有上下文也能基于常识作答，
            # 只是要明确说明"没有找到知识库依据"。把检索失败升级成整单失败，
            # 等于让一个可降级的功能拖垮主链路。
            return {"contexts": [], "extra": {"retrieve_error": result.get("error", "")}}
        payload = result.get("result") or {}
        return {"contexts": payload.get("contexts", [])}

    async def generate_node(state: AgentState) -> dict[str, Any]:
        contexts = state.get("contexts") or []
        context_text = (
            "\n".join(
                f"[{i + 1}] ({c.get('source', '?')}) {c.get('text', '')}"
                for i, c in enumerate(contexts)
            )
            if contexts
            else "（知识库未返回相关片段）"
        )
        prompt = cluster.prompts.render(
            "rag_answer",
            persona="跨境电商售前顾问",
            question=state["query"],
            context=context_text,
        )
        content, meta = await cluster.gateway.complete(prompt, state["query"])
        parsed = parse_json_lenient(content)
        if isinstance(parsed, dict) and parsed.get("answer"):
            return {
                "answer": str(parsed["answer"]),
                "used_sources": list(parsed.get("used_sources") or []),
                "need_human": bool(parsed.get("need_human")),
                "extra": {**(state.get("extra") or {}), "model": meta.get("model")},
            }
        # 结构不对也照样把文本给用户 —— 降级成纯文本比报错体验好
        return {
            "answer": (content or "").strip(),
            "used_sources": [],
            "need_human": False,
            "extra": {**(state.get("extra") or {}), "model": meta.get("model")},
        }

    graph = StateGraph(AgentState)
    graph.add_node("intent", intent_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.set_entry_point("intent")
    graph.add_edge("intent", "retrieve")
    graph.add_edge("retrieve", "generate")

    if post_node is not None:
        graph.add_node("post", post_node)
        graph.add_edge("generate", "post")
        graph.add_edge("post", END)
    else:
        graph.add_edge("generate", END)

    return graph.compile()


class GraphAgent:
    """状态图的执行入口，对外暴露与 ReActLoop 一致的 `run()` 契约。"""

    def __init__(
        self,
        cluster: SharedCluster,
        *,
        store: TraceStore | None = None,
        post_node: PostNode | None = None,
    ) -> None:
        self.cluster = cluster
        self.store = store
        self.post_node = post_node
        self._app = build_graph(cluster, post_node=post_node)

    async def run(
        self, query: str, *, session_id: str = "default", record: bool = True
    ) -> Trace:
        history = self.cluster.session_memory.messages(session_id)
        final: dict[str, Any] = await self._app.ainvoke(
            {
                "query": query,
                "session_id": session_id,
                "history": history,
                "domain": "",
                "contexts": [],
                "answer": "",
                "extra": {},
            }
        )

        trace = Trace(
            query=query,
            engine="langgraph",
            steps=[
                {"node": "intent", "domain": final.get("domain")},
                {"node": "retrieve", "hits": len(final.get("contexts") or [])},
                {"node": "generate", "model": (final.get("extra") or {}).get("model")},
            ],
            tools_called=["rag_search"],
            iterations=3,
            answer=final.get("answer", ""),
            extra={
                "domain": final.get("domain", ""),
                "used_sources": final.get("used_sources", []),
                "need_human": final.get("need_human", False),
                **(final.get("extra") or {}),
            },
        )
        trace.failure_mode = classify_failure(
            steps=trace.steps,
            tools_called=trace.tools_called,
            tool_errors=(
                [(final.get("extra") or {}).get("retrieve_error", "")]
                if (final.get("extra") or {}).get("retrieve_error")
                else []
            ),
            parse_errors=0,
            iterations=3,
            max_iterations=MAX_ITERATIONS_DEFAULT,
            answer=trace.answer,
        )
        trace.outcome = "fail" if trace.failure_mode else "ok"

        if session_id and trace.ok:
            self.cluster.session_memory.append(session_id, "user", query)
            self.cluster.session_memory.append(session_id, "assistant", trace.answer)

        if record and self.store is not None:
            self.store.add(trace)
        return trace


__all__ = ["AgentState", "GraphAgent", "build_graph", "DOMAINS"]
