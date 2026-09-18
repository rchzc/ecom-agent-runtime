"""ReAct Loop 单测：四个边界条件 + 策略补丁真的能救回失败。

这一组是"自进化"的地基 —— **必须能稳定造出失败，才谈得上验证修复**。
所以这里用一种很直接的手段造失败：把工具换成一个每次都抛异常的版本。
换成"伪造几条失败轨迹"就测不出策略补丁到底有没有用。
"""
from __future__ import annotations

import pytest
from ecom_shared.gateway.mock import MockLLMGateway

from ecom_runtime.loop import ReActLoop


def break_tool(runtime, name: str = "rag_search") -> None:
    """故障注入：把某个工具换成永远抛异常的版本。"""

    def boom(*_args, **_kwargs):
        raise RuntimeError("工具故障注入")

    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    runtime.cluster.tools.unregister(name)
    runtime.cluster.tools.register(name, "故障注入版本", schema, boom, owner="test")


def force_tool_calls(runtime, tool: str = "rag_search") -> None:
    """让 mock 网关**每一轮都发起工具调用**，包括工具已经成功返回之后。

    只在两种场景下需要它：
    1. 复现"工具全都正常、模型却一直要调"的**死循环**（迭代上限的那道闸）；
    2. 指定一个**不存在的**工具名（`tool="not_a_real_tool"`）来测幻觉归因。

    工具报错的场景**不要**用它：mock 自己就会在 ok=false 的观察值上重试（跟真模型一样），
    那条路径应当被真实地走到，靠模板硬撑等于绕开了真正要验证的机制。

    关键词特意选 `可用工具`：它只出现在**声明了工具清单**的 system prompt 里，
    循环主动禁用工具、改成强制收尾的提示词中不含这三个字，
    所以模板不会误伤"禁用工具后应当能收尾"的那一轮。
    """
    gw = runtime.cluster.gateway
    gw.register_template("可用工具", {"action": "tool", "tool": tool, "args": {"query": "q"}})


def test_mock_gateway_is_used_offline(runtime):
    """离线之所以能测，是因为 mock 是显式 provider —— 不是"没 Key 时悄悄兜底"。"""
    assert isinstance(runtime.cluster.gateway, MockLLMGateway)
    assert runtime.cluster.settings.is_mock


async def test_loop_completes_a_full_react_cycle(runtime):
    trace = await runtime.loop.run("推荐一款适合运动的降噪耳机")
    assert trace.ok
    assert trace.failure_mode == ""
    assert trace.answer
    # mock 网关先发起一次检索、拿到观察值后再收尾 → 至少两轮
    assert trace.iterations >= 2
    assert "rag_search" in trace.tools_called


async def test_loop_records_trace_into_store(runtime):
    trace = await runtime.loop.run("这个 listing 怎么写")
    assert runtime.store.get(trace.trace_id) is trace


async def test_loop_skips_recording_when_asked(runtime):
    trace = await runtime.loop.run("广告预算怎么分配", record=False)
    assert runtime.store.get(trace.trace_id) is None


async def test_broken_tool_produces_repeat_tool_error(runtime):
    """同一个工具连着失败 → 归到"反复撞同一个工具报错"，而不是笼统的工具错误。

    注意这里**没有** force_tool_calls：mock 看到 ok=false 会自己重试，
    跟真模型的行为一致。所以这个用例走的是真实路径，不是被模板架着走的。
    """
    break_tool(runtime)
    trace = await runtime.loop.run("ACOS 太高怎么办")
    assert trace.outcome == "fail"
    assert trace.failure_mode == "repeat_tool_error"
    assert trace.answer == ""
    assert trace.iterations == runtime.loop.max_iterations


async def test_single_tool_failure_is_attributed_distinctly(runtime):
    """只失败一次时归到 tool_error —— 与"反复失败"分开，两条补丁的针对性才不一样。"""
    break_tool(runtime)
    loop = ReActLoop(runtime.cluster, store=None, max_iterations=1)
    trace = await loop.run("ACOS 太高怎么办")
    assert trace.outcome == "fail"
    assert trace.failure_mode == "tool_error"


async def test_iteration_limit_stops_dead_loop(runtime):
    """工具都正常、但模型一直要调工具 —— 上限是死循环的最后一道闸。"""
    force_tool_calls(runtime)
    loop = ReActLoop(runtime.cluster, store=runtime.store, max_iterations=2)
    trace = await loop.run("ACOS 太高怎么办")
    assert trace.outcome == "fail"
    assert trace.failure_mode == "iteration_exhausted"
    assert trace.iterations == 2


async def test_single_unknown_tool_is_attributed(runtime):
    """模型幻觉出不存在的工具时，错误要能归到 unknown_tool（只出现一次的情况）。"""
    force_tool_calls(runtime, tool="not_a_real_tool")
    loop = ReActLoop(runtime.cluster, store=None, max_iterations=1)
    trace = await loop.run("随便问点什么")
    assert trace.outcome == "fail"
    assert trace.failure_mode == "unknown_tool"


# ---------------------------------------------------------------------------
# 策略补丁：这一组是自进化有没有意义的关键
# ---------------------------------------------------------------------------
async def test_error_budget_policy_recovers_dead_loop(runtime):
    """**同一个坏工具 + 一个错误预算策略 → 从必败变成能答出来。**

    这是"Prompt 补丁"和"策略补丁"的分界：光在提示词里写"不要重复调用"，
    模型下一轮照样会调，因为拦住它的是循环本身。
    """
    break_tool(runtime)

    failed = await runtime.loop.run("ACOS 太高怎么办")
    assert failed.outcome == "fail"

    recovered = await runtime.loop.run(
        "ACOS 太高怎么办", policy={"max_tool_errors_per_tool": 1}
    )
    assert recovered.outcome == "ok", recovered.failure_mode
    assert recovered.answer
    # 预算用尽后循环主动禁用工具，而不是继续撞墙
    assert recovered.extra["tools_forbidden"] is True
    assert any(s.get("action") == "tool_skipped" for s in recovered.steps)


async def test_policy_iteration_cap_is_respected(runtime):
    force_tool_calls(runtime)
    trace = await runtime.loop.run(
        "ACOS 太高怎么办", policy={"max_iterations": 2, "max_tool_errors_per_tool": 1}
    )
    assert trace.iterations <= 2


async def test_replay_turns_failures_into_passes(runtime):
    """端到端：制造失败 → 提补丁 → 回放 → 通过率真的上升。

    和 `scripts/evolve.py` 演示的是同一条链路，只是查询集更小、断言更硬。
    这里是"自进化"这件事唯一有说服力的证据：同一批查询、同一批故障，
    只有通过率真的从 0 变到 1，补丁才算数。
    """
    break_tool(runtime)
    queries = ["ACOS 太高怎么办", "这个类目还能不能进", "差评率 4% 怎么处理"]
    for query in queries:
        await runtime.run(query, engine="react-loop", session_id="t")

    patches = runtime.evolution.analyze()
    assert patches, "应当至少分析出一条补丁"
    patch = next(p for p in patches if p.mode == "repeat_tool_error")
    assert patch.policy.get("max_tool_errors_per_tool") == 1

    report = await runtime.replay(patch)
    assert report.total == len(queries)
    assert report.before_rate == 0.0
    assert report.after_rate == 1.0
    assert report.effective is True
    assert len(report.fixed) == len(queries)


# ---------------------------------------------------------------------------
# 会话记忆
# ---------------------------------------------------------------------------
async def test_session_memory_accumulates_only_on_success(runtime):
    await runtime.loop.run("推荐一款耳机", session_id="s1")
    turns = runtime.cluster.session_memory.history("s1")
    assert [t.role for t in turns] == ["user", "assistant"]


async def test_session_memory_not_polluted_by_failed_turn(runtime):
    """失败轮次不写进会话记忆 —— 否则用户下一句"它"会指向一个没答上来的问题。"""
    break_tool(runtime)
    trace = await runtime.loop.run("ACOS 太高怎么办", session_id="s2")
    assert trace.outcome == "fail"
    assert runtime.cluster.session_memory.history("s2") == []


async def test_history_is_fed_into_later_turns(runtime):
    first = await runtime.loop.run("推荐一款降噪耳机", session_id="s3")
    second = await runtime.loop.run("它有什么优惠", session_id="s3")
    assert first.ok and second.ok
    # 第二轮的用户提问里应带上历史，供指代解析使用
    assert runtime.cluster.session_memory.last_user_query("s3") == "它有什么优惠"


@pytest.mark.parametrize("max_iterations", [1, 2, 4])
async def test_loop_never_exceeds_iteration_cap(runtime, max_iterations):
    loop = ReActLoop(runtime.cluster, store=None, max_iterations=max_iterations)
    trace = await loop.run("广告 ACOS 怎么降")
    assert trace.iterations <= max_iterations


# ---------------------------------------------------------------------------
# 意图路由：Loop 这条路径必须在入口自己补上，没有 intent 节点可以依赖
# ---------------------------------------------------------------------------
async def test_loop_records_routed_domain_on_trace(runtime):
    """两条引擎产出的 Trace 字段要一致 —— 走状态图时 domain 由 intent 节点填。"""
    trace = await runtime.loop.run("广告预算怎么分配，ACOS 太高了")
    assert trace.extra["domain"] == "ads"


async def test_loop_injects_routed_domain_into_rag_search(runtime, rag_spy):
    """模型没指定域时由编排层兜底补上，检索才真的带了域过滤。"""
    await runtime.loop.run("广告预算怎么分配，ACOS 太高了")
    assert rag_spy and rag_spy[0]["domain"] == "ads"


async def test_loop_injects_unrestricted_domain_when_route_is_general(runtime, rag_spy):
    """路由没命中具体域时传 `*`（不限域），不能把 general 当目录过滤。"""
    await runtime.loop.run("今天天气不错")
    assert rag_spy and rag_spy[0]["domain"] == "*"


async def test_loop_does_not_override_model_specified_domain(runtime, rag_spy):
    """编排层只兜底、不越权：模型自己指定域时照它的来。

    模型可能比关键词规则更懂这次该查哪个域（规则只看字面命中），
    硬覆盖会把模型的判断力压掉 —— 兜底和越权的分界线就在这里。
    """
    runtime.cluster.gateway.register_template(
        "可用工具",
        {"action": "tool", "tool": "rag_search", "args": {"query": "q", "domain": "logistics"}},
    )
    loop = ReActLoop(runtime.cluster, store=None, max_iterations=1)
    await loop.run("广告预算怎么分配")  # 路由会判成 ads
    assert rag_spy and rag_spy[0]["domain"] == "logistics"
