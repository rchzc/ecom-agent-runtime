"""测试夹具：**全部离线**，不读 .env、不联网、不依赖任何 API Key。

三条硬约束，都是踩过之后加的：

1. `env_file=None` + `load_env_file=False` —— 一旦测试里读了仓库根的 .env，
   "缺 Key 应当报错"这类用例会被 .env 把值填回来，测试看着在跑其实什么都没验证。
2. 目录全部指向临时目录 —— 否则测试会往开发用的 vector_store 里写索引，
   本地数据被测试污染（而且这类写入是不可逆的）。
3. provider=mock + backend=lexical —— CI 上没有 Key 也不能 skip。
   mock 是**显式 provider**，不是"没配 Key 时的兜底"，所以测试要显式声明它。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ecom_runtime import AgentRuntime  # noqa: E402
from ecom_runtime.agents import PresaleAgent  # noqa: E402
from ecom_runtime.config import load_runtime_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_env(tmp_path, monkeypatch):
    """把配置钉死在离线环境。autouse 保证没有用例能"漏网"去联网。"""
    for key, value in {
        "LLM_PROVIDER": "mock",
        "LLM_API_KEY": "",
        "API_KEY": "",
        "DASHSCOPE_API_KEY": "",
        "VECTOR_BACKEND": "lexical",
        "DOCS_DIR": str(tmp_path / "docs"),
        "VECTOR_DIR": str(tmp_path / "vector"),
        "TRACE_DIR": str(tmp_path / "traces"),
        "MAX_ITERATIONS": "4",
        "LOG_LEVEL": "WARNING",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def settings(tmp_path):
    """离线配置快照。不读 .env —— 测试的输入必须只有环境变量。"""
    return load_runtime_settings(env_file=None, load_env_file=False)


@pytest.fixture
def runtime(settings, docs_dir):
    """一个装好售前 Agent、且**索引已建好**的底座。

    建索引不是可选项：知识库为空时检索会明确报错（而不是返回噪声），
    于是所有"检索链路能不能跑通"的用例都会退化成在测降级分支。
    与其让每个用例自己记得 ingest，不如让夹具保证一个可用的起点。
    """
    import asyncio

    from ecom_runtime.mcp_server import build_runtime

    rt = build_runtime()
    asyncio.run(rt.ingest())
    return rt


@pytest.fixture
def bare_runtime(settings, docs_dir):
    """不装任何业务 Agent 的裸底座 —— 用于验证"没有后置节点时不产出素材"。"""
    from ecom_runtime.mcp_server import build_runtime

    return build_runtime(with_presale=False)


@pytest.fixture
def agent(runtime):
    return PresaleAgent.install(runtime)


@pytest.fixture
def rag_spy(runtime):
    """把 rag_search 换成**记录入参**的替身，返回它收到的调用列表。

    用来断言编排层到底把哪个 `domain` 传下去了 —— 这是"按域检索"是否真的生效的唯一
    可观测证据：只看最终答案的话，不限域和过滤到空域都可能答得出来，看不出区别。

    loop 与 graph 都经由工具协议取检索结果，所以一个替身两条路径通用。
    """
    seen: list[dict] = []

    async def spy(query: str, domain: str = "*", top_k: int = 4):
        seen.append({"query": query, "domain": domain, "top_k": top_k})
        return {"count": 0, "domain": domain, "contexts": []}

    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "domain": {"type": "string"}},
        "required": ["query"],
    }
    runtime.cluster.tools.unregister("rag_search")
    runtime.cluster.tools.register("rag_search", "检索替身", schema, spy, owner="test")
    return seen


@pytest.fixture
def docs_dir(tmp_path):
    """两篇最小文档，够验证"按域建索引 + 按域检索"即可。"""
    root = tmp_path / "docs"
    (root / "ads").mkdir(parents=True)
    (root / "selection").mkdir(parents=True)
    (root / "ads" / "acos.md").write_text(
        "ACOS 是广告花费占销售额的比例。ACOS 过高时先看关键词匹配是否过宽，"
        "再检查竞价是否超出毛利空间。\n\n"
        "降低 ACOS 的常见做法：否定高花费零转化词、把预算挪到转化率高的时段。",
        encoding="utf-8",
    )
    (root / "selection" / "niche.md").write_text(
        "选品先看类目容量与竞争度，再看自己能不能做出差异化卖点。\n\n"
        "验证需求的方法：看头部链接的评论增速，而不是只看总量。",
        encoding="utf-8",
    )
    return str(root)
