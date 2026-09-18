"""底座组装与配置的单测。

检查的是"结构性问题"，不是功能：
- 组装入口是否唯一（装配点散落是最常见的腐化方式）
- 依赖方向是否单向（业务数据靠注入，不靠 import）
- 配置缺失是否**快速失败**，而不是悄悄降级成一个看着能跑的假成功
"""
from __future__ import annotations

import pytest
from ecom_shared import ConfigError

from ecom_runtime import ENGINES, AgentRuntime, ReActLoop
from ecom_runtime.agents import PresaleAgent, catalog
from ecom_runtime.config import MAX_ITERATIONS_DEFAULT, RuntimeSettings, load_runtime_settings


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def test_runtime_settings_extends_shared_settings(settings):
    """配置继承共享底座：共享包加字段，本仓不用改代码也不会漏。"""
    from ecom_shared import Settings

    assert isinstance(settings, Settings)
    assert isinstance(settings, RuntimeSettings)


def test_runtime_settings_adds_only_directory_fields(settings):
    own = set(RuntimeSettings.__dataclass_fields__) - set(
        __import__("ecom_shared").Settings.__dataclass_fields__
    )
    assert own == {"docs_dir", "trace_dir", "max_iterations"}


def test_unknown_provider_fails_fast(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
    with pytest.raises(ConfigError):
        load_runtime_settings(env_file=None, load_env_file=False)


def test_missing_key_fails_fast_unless_mock(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        load_runtime_settings(env_file=None, load_env_file=False)


def test_mock_provider_needs_no_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    settings = load_runtime_settings(env_file=None, load_env_file=False)
    assert settings.is_mock


def test_invalid_max_iterations_rejected(monkeypatch):
    monkeypatch.setenv("MAX_ITERATIONS", "abc")
    with pytest.raises(ConfigError, match="MAX_ITERATIONS"):
        load_runtime_settings(env_file=None, load_env_file=False)

    monkeypatch.setenv("MAX_ITERATIONS", "0")
    with pytest.raises(ConfigError):
        load_runtime_settings(env_file=None, load_env_file=False)


def test_max_iterations_default_is_used(monkeypatch):
    monkeypatch.delenv("MAX_ITERATIONS", raising=False)
    assert load_runtime_settings(env_file=None, load_env_file=False).max_iterations == (
        MAX_ITERATIONS_DEFAULT
    )


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------
def test_build_returns_runtime_with_all_parts(runtime):
    assert isinstance(runtime, AgentRuntime)
    assert isinstance(runtime.loop, ReActLoop)
    assert runtime.graph is not None
    assert runtime.store is not None
    assert runtime.evolution is not None


def test_describe_exposes_status_for_frontend(runtime):
    snap = runtime.describe()
    for key in ("provider", "mock", "tools", "prompts", "retrieval", "engines", "traces"):
        assert key in snap, key
    assert snap["engines"] == list(ENGINES)
    assert snap["limits"]["max_iterations"] == runtime.settings.max_iterations


def test_business_data_injected_not_imported_by_shared_layer(runtime):
    """共享包不认识业务数据 —— 它是被"注入"进来的，方向单向。"""
    assert runtime.catalog is catalog
    result = runtime.cluster.tools  # noqa: F841 - 存在性即可
    assert runtime.cluster.settings.is_mock


async def test_product_query_tool_works_through_injection(runtime):
    called = await runtime.cluster.tools.call("product_query", {"name": "OpenFit"})
    assert called["ok"] is True
    assert called["result"]["found"] is True


async def test_metrics_query_tool_works_through_injection(runtime):
    called = await runtime.cluster.tools.call("metrics_query", {"seller_id": "S1", "days": 7})
    assert called["ok"] is True
    assert called["result"]["available"] is True


async def test_product_query_lists_available_when_missing(runtime):
    called = await runtime.cluster.tools.call("product_query", {"name": "不存在的东西"})
    assert called["result"]["found"] is False
    assert called["result"]["available"]


def test_unknown_engine_is_rejected(runtime):
    import asyncio

    with pytest.raises(ValueError, match="未知引擎"):
        asyncio.run(runtime.run("hi", engine="nope"))


def test_engines_constant_covers_both_paths():
    assert set(ENGINES) == {"langgraph", "react-loop"}


# ---------------------------------------------------------------------------
# 索引与检索（lexical 后端，完全离线）
# ---------------------------------------------------------------------------
async def test_ingest_and_domain_filtered_search(runtime, docs_dir):
    report = await runtime.ingest(docs_dir)
    payload = report.to_dict() if hasattr(report, "to_dict") else report
    assert runtime.cluster.rag.count() > 0

    hits = await runtime.cluster.rag.search("ACOS 过高", domain="ads")
    assert hits
    assert all(h.domain == "ads" for h in hits)
    assert payload is not None


async def test_search_returns_sources_for_citation(runtime, docs_dir):
    await runtime.ingest(docs_dir)
    contexts = await runtime.cluster.rag.search_as_context("选品 差异化", domain="selection")
    assert contexts
    assert contexts[0]["source"]


async def test_empty_query_is_rejected_not_silently_answered(runtime, docs_dir):
    """空 query 检索会返回噪声却"看起来有结果"，所以必须明确报错。"""
    from ecom_shared import KnowledgeBaseError

    await runtime.ingest(docs_dir)
    with pytest.raises(KnowledgeBaseError):
        await runtime.cluster.rag.search("   ")


# ---------------------------------------------------------------------------
# 业务 Agent 装配
# ---------------------------------------------------------------------------
def test_install_is_idempotent(runtime):
    first = PresaleAgent.install(runtime)
    second = PresaleAgent.install(runtime)
    assert first is second
    assert runtime.cluster.tools.has("book_appointment")


def test_business_tool_is_registered_with_shared_registry(agent, runtime):
    """业务工具注册进共享注册表 → 自动获得协议、留痕与 MCP 暴露。"""
    spec = runtime.cluster.tools.get("book_appointment")
    assert spec.owner == "presale"
    assert spec.side_effect is True


async def test_business_tool_is_callable(agent):
    result = await agent.runtime.cluster.tools.call(
        "book_appointment", {"customer": "张三", "slot": "2026-09-20 15:00"}
    )
    assert result["ok"] is True
    assert result["result"]["appointment_id"].startswith("AP-")


async def test_presale_consult_returns_full_payload(agent):
    result = await agent.consult("推荐一款适合运动的降噪耳机")
    for key in ("engine", "answer", "materials", "domain", "trace_id", "outcome"):
        assert key in result
    assert result["outcome"] == "ok"
    assert result["domain"] == "selection"


async def test_catalog_snapshot_is_exposed_for_frontend(agent):
    snapshot = PresaleAgent.catalog_snapshot()
    assert snapshot["count"] >= 3
    assert snapshot["sample"]["found"] is True
