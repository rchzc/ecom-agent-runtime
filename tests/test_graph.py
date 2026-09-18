"""LangGraph 编排单测：节点顺序、降级行为、后置节点。

状态图这条路径的价值在于"流程由代码决定"，所以测试的重点是
**节点是否按声明的顺序跑、以及单个节点失败时会不会拖垮整条链路**。
"""
from __future__ import annotations

from ecom_runtime.graph import GraphAgent, build_graph
from ecom_runtime.loop import ReActLoop


async def test_graph_runs_intent_retrieve_generate(runtime):
    trace = await runtime.graph.run("推荐一款适合运动的降噪耳机")
    assert trace.engine == "langgraph"
    assert trace.ok
    assert trace.answer
    assert [s["node"] for s in trace.steps] == ["intent", "retrieve", "generate"]


async def test_graph_records_domain_and_branch(runtime):
    trace = await runtime.graph.run("广告 ACOS 太高了")
    assert trace.extra["domain"] == "ads"
    assert trace.steps[0]["domain"] == "ads"


async def test_graph_searches_unrestricted_when_route_is_general(runtime, rag_spy):
    """路由判成 general 时不能拿它去当过滤条件。

    知识库底下只有 selection/ads/review/... 这些目录，没有 general，
    用 general 过滤等于零命中 —— "没识别出域"反而比不限域检索得更差。
    路由结果本身照样记进 Trace（观测需要），但传给检索的要翻成不限域。
    """
    trace = await runtime.graph.run("今天天气不错")
    assert trace.extra["domain"] == "general"
    assert rag_spy and rag_spy[0]["domain"] == "*"


async def test_graph_without_post_node_returns_no_materials(bare_runtime):
    """没挂后置节点时不该凭空多出素材字段 —— 后置节点是业务侧显式挂上去的。"""
    trace = await bare_runtime.graph.run("这个类目还能不能进")
    assert "materials" not in trace.extra
    assert trace.ok


async def test_post_node_attaches_materials(runtime, agent):
    """售前 Agent 装上后置节点后，答完顺带产出素材草稿。"""
    trace = await runtime.graph.run("推荐一款适合运动的降噪耳机")
    materials = trace.extra.get("materials") or {}
    assert materials.get("image_prompt")
    assert materials.get("video_script")
    assert materials.get("live_talk")


async def test_retrieve_failure_degrades_instead_of_aborting(runtime, docs_dir):
    """检索挂掉时链路要能降级继续（只是没有知识库依据），而不是整单失败。

    把一个可降级的功能升级成致命错误，是这类编排里最常见的过度反应。
    """
    result = await runtime.ingest(docs_dir)
    assert result is not None

    def boom(*_a, **_k):
        raise RuntimeError("检索服务不可用")

    runtime.cluster.tools.unregister("rag_search")
    runtime.cluster.tools.register(
        "rag_search",
        "故障版本",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        boom,
        owner="test",
    )
    trace = await runtime.graph.run("ACOS 太高怎么办")
    assert trace.extra.get("retrieve_error")
    assert trace.steps[1]["hits"] == 0


async def test_graph_and_loop_share_the_same_trace_shape(runtime):
    """两条引擎产出同一种 Trace，自进化才能同时覆盖它们。"""
    graph_trace = await runtime.run("这个 listing 怎么写", engine="langgraph")
    loop_trace = await runtime.run("这个 listing 怎么写", engine="react-loop")
    assert type(graph_trace) is type(loop_trace)
    assert {graph_trace.engine, loop_trace.engine} == {"langgraph", "react-loop"}
    assert runtime.store.get(graph_trace.trace_id) is graph_trace


async def test_build_graph_is_compiled_and_runnable(runtime):
    app = build_graph(runtime.cluster)
    final = await app.ainvoke({"query": "广告预算怎么分配", "domain": "", "contexts": [], "answer": ""})
    assert final["answer"]


async def test_graph_agent_is_swappable_for_loop(runtime):
    """同一批查询换引擎跑，结果字段一致 —— 业务侧换引擎不用改调用代码。"""
    loop = ReActLoop(runtime.cluster, store=None)
    a = await loop.run("这个类目还能不能进")
    b = await GraphAgent(runtime.cluster, store=None).run("这个类目还能不能进")
    assert a.answer and b.answer
    assert a.query == b.query
