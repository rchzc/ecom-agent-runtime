"""自进化闭环单测：失败归因、轨迹库、补丁建议、回放对比。

这套测试的重点是**判据本身**是否可靠 ——
自进化最怕的是"以为自己修好了"，所以每条断言都盯着"通过率是不是真的变了"。
"""
from __future__ import annotations

from ecom_runtime.evolution import (
    FAILURE_MODES,
    PromptPatch,
    Trace,
    TraceStore,
    classify_failure,
    propose_patches,
    replay_failures,
)


def make(**kwargs):
    base = dict(
        steps=[{"iter": 1, "action": "answer"}],
        tools_called=[],
        tool_errors=[],
        parse_errors=0,
        iterations=1,
        max_iterations=4,
        answer="正常回答",
    )
    base.update(kwargs)
    return classify_failure(**base)


# ---------------------------------------------------------------------------
# 失败归因
# ---------------------------------------------------------------------------
def test_no_failure_when_answer_present():
    assert make() == ""


def test_tool_errors_during_successful_run_are_not_failures():
    """中间撞过工具报错但最终答出来了 —— 不算失败。

    把过程噪声记成失败会让失败率虚高，之后的补丁就会针对伪问题。
    """
    assert make(tool_errors=["boom"], answer="答出来了") == ""


def test_parse_errors_reported_when_no_answer():
    assert make(parse_errors=2, answer="") == "unparsable_output"


def test_repeat_tool_error_beats_single_tool_error():
    mode = make(tool_errors=["boom", "boom"], iterations=4, answer="")
    assert mode == "repeat_tool_error"


def test_unknown_tool_detected():
    assert make(tool_errors=["unknown tool: foobar"], answer="") == "unknown_tool"


def test_single_tool_error_reported():
    assert make(tool_errors=["boom"], answer="") == "tool_error"


def test_iteration_exhausted_reported():
    assert make(iterations=4, answer="") == "iteration_exhausted"


def test_empty_steps_falls_back_to_empty_answer():
    assert make(steps=[], iterations=0, answer="") == "empty_answer"


def test_every_failure_mode_has_a_label():
    for mode in FAILURE_MODES:
        assert FAILURE_MODES[mode]


# ---------------------------------------------------------------------------
# 轨迹库
# ---------------------------------------------------------------------------
def test_store_stats_and_grouping():
    store = TraceStore()
    store.add(Trace(query="a", engine="react-loop", answer="ok"))
    store.add(
        Trace(query="b", engine="react-loop", outcome="fail", failure_mode="tool_error")
    )
    store.add(
        Trace(query="c", engine="react-loop", outcome="fail", failure_mode="tool_error")
    )
    stats = store.stats()
    assert stats["total"] == 3
    assert stats["ok"] == 1
    assert stats["by_mode"] == {"tool_error": 2}
    assert stats["pass_rate"] == round(1 / 3, 3)


def test_store_persists_to_jsonl_and_reloads(tmp_path):
    path = str(tmp_path / "traces.jsonl")
    store = TraceStore(path)
    store.add(Trace(query="a", engine="react-loop", answer="ok"))
    store.add(Trace(query="b", engine="graph", outcome="fail", failure_mode="empty_answer"))

    reloaded = TraceStore(path)
    assert len(reloaded.all()) == 2
    assert reloaded.failures()[0].failure_mode == "empty_answer"


def test_store_clear_removes_file(tmp_path):
    import os

    path = str(tmp_path / "traces.jsonl")
    store = TraceStore(path)
    store.add(Trace(query="a", engine="react-loop"))
    store.clear()
    assert store.all() == []
    assert not os.path.exists(path)


def test_store_get_by_id():
    store = TraceStore()
    trace = store.add(Trace(query="a", engine="react-loop"))
    assert store.get(trace.trace_id) is trace
    assert store.get("nope") is None


# ---------------------------------------------------------------------------
# 补丁建议
# ---------------------------------------------------------------------------
def test_propose_patches_carries_evidence_and_policy():
    store = TraceStore()
    for i in range(3):
        store.add(
            Trace(query=f"q{i}", engine="react-loop", outcome="fail", failure_mode="repeat_tool_error")
        )
    patches = propose_patches(store)
    assert len(patches) == 1
    patch = patches[0]
    assert len(patch.evidence) == 3
    # 关键：这一类的失败光靠 Prompt 修不掉，必须带编排策略
    assert patch.policy.get("max_tool_errors_per_tool") == 1
    assert "两次" in patch.rule or "停止" in patch.rule


def test_propose_patches_sorted_by_evidence_count():
    store = TraceStore()
    for i in range(2):
        store.add(Trace(query=f"a{i}", engine="loop", outcome="fail", failure_mode="tool_error"))
    store.add(Trace(query="b", engine="loop", outcome="fail", failure_mode="empty_answer"))
    patches = propose_patches(store)
    assert [len(p.evidence) for p in patches] == [2, 1]


def test_patch_render_includes_mode_label():
    patch = PromptPatch(mode="tool_error", rule="规则", evidence=["t1"])
    assert "工具调用返回失败" in patch.render()
    assert "规则" in patch.render()


# ---------------------------------------------------------------------------
# 回放对比
# ---------------------------------------------------------------------------
async def test_replay_reports_rate_increase_for_fixed_cases():
    store = TraceStore()
    for i in range(3):
        store.add(
            Trace(query=f"q{i}", engine="loop", outcome="fail", failure_mode="tool_error")
        )

    async def run_once(query, patch):
        return Trace(
            query=query,
            engine="loop",
            answer="修好了" if patch else "",
            outcome="ok" if patch else "fail",
            failure_mode="" if patch else "tool_error",
        )

    report = await replay_failures(run_once, store, patch=PromptPatch("tool_error", "r"))
    assert report.total == 3
    assert report.before_rate == 0.0
    assert report.after_rate == 1.0
    assert report.effective is True
    assert len(report.fixed) == 3


async def test_replay_marks_patch_ineffective_when_nothing_changes():
    """补丁没让通过率上升就是无效 —— 不能因为"跑过了"就当成修好了。"""
    store = TraceStore()
    store.add(Trace(query="q", engine="loop", outcome="fail", failure_mode="tool_error"))

    async def run_once(query, patch):
        return Trace(query=query, engine="loop", outcome="fail", failure_mode="tool_error")

    report = await replay_failures(run_once, store, patch=PromptPatch("tool_error", "r"))
    assert report.effective is False
    assert report.after_rate == report.before_rate == 0.0
    assert report.still_failing


async def test_replay_uses_the_same_query_set_not_a_new_one():
    """回放必须打原来那批查询 —— 换一批比通过率等于没有对照组。"""
    store = TraceStore()
    store.add(Trace(query="原始问题", engine="loop", outcome="fail", failure_mode="tool_error"))
    seen: list[str] = []

    async def run_once(query, patch):
        seen.append(query)
        return Trace(query=query, engine="loop", answer="x")

    await replay_failures(run_once, store, patch=PromptPatch("tool_error", "r"))
    assert seen == ["原始问题"]


async def test_replay_can_filter_by_mode_and_limit():
    store = TraceStore()
    store.add(Trace(query="a", engine="loop", outcome="fail", failure_mode="tool_error"))
    store.add(Trace(query="b", engine="loop", outcome="fail", failure_mode="empty_answer"))

    async def run_once(query, patch):
        return Trace(query=query, engine="loop", answer="x")

    report = await replay_failures(
        run_once, store, patch=PromptPatch("tool_error", "r"), modes=["tool_error"]
    )
    assert report.total == 1

    report2 = await replay_failures(run_once, store, patch=PromptPatch("tool_error", "r"), limit=1)
    assert report2.total == 1
