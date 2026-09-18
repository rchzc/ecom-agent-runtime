"""运行时底座门面：一次组装，业务侧拿到全套能力。

    runtime = AgentRuntime.build()
    trace = await runtime.run("推荐一款降噪耳机", engine="react-loop")
    print(runtime.describe())

**没有这一层会怎样**：每个业务 Agent 各写一遍"建 cluster → 建轨迹库 → 建两条引擎 → 注册
自己的业务工具"，于是"售前 Agent 忘了开轨迹留痕""内容 Agent 的迭代上限是 8 而别处是 4"
这类不一致就会长出来。组装逻辑收敛到一个入口，这些差异就不可能悄悄出现。

依赖方向在 `pyproject.toml` 里写死：本仓依赖 `ecom-agent-shared`，共享包不认识任何业务包。
业务数据（商品、经营指标）通过**依赖注入**进来，而不是让共享层 import 业务模块 ——
`product_provider` 就是这条边界的实物。
"""
from __future__ import annotations

import os
from typing import Any

from ecom_shared import SharedCluster

from .config import DEFAULT_ENV_FILE, RuntimeSettings, load_runtime_settings
from .evolution import EvolutionEngine, PromptPatch, ReplayReport, Trace, TraceStore
from .graph import GraphAgent, PostNode
from .loop import ReActLoop

ENGINES = ("langgraph", "react-loop")


class AgentRuntime:
    """运行时底座。持有共享集群、轨迹库与两条编排引擎。"""

    def __init__(
        self,
        settings: RuntimeSettings,
        cluster: SharedCluster,
        store: TraceStore,
        *,
        catalog: Any | None = None,
        post_node=None,
    ) -> None:
        self.settings = settings
        self.cluster = cluster
        self.store = store
        self.catalog = catalog
        self.loop = ReActLoop(cluster, store=store, max_iterations=settings.max_iterations)
        self.graph = GraphAgent(cluster, store=store, post_node=post_node)
        self.evolution = EvolutionEngine(store)

    # ------------------------------------------------------------------
    @classmethod
    def build(
        cls,
        settings: RuntimeSettings | None = None,
        *,
        env_file: str | None = None,
        trace_file: str | None = None,
        catalog: Any | None = None,
        post_node=None,
        persist_traces: bool = False,
        **cluster_kwargs: Any,
    ) -> "AgentRuntime":
        """组装底座。

        `persist_traces=True` 时轨迹落 JSONL —— 自进化要跨进程复盘，
        必须能读回上一次的失败样本，否则每次启动都是一张白纸。
        """
        if settings is None:
            # 没显式给 env_file 时：仓库根有 .env 就用它，没有就只按进程环境变量解析。
            # 不硬塞一个不存在的路径 —— load_dotenv 收到不存在的文件时是静默无操作，
            # 看上去"读了配置"其实什么也没读，排查起来很费时间。
            if env_file is None and os.path.exists(DEFAULT_ENV_FILE):
                env_file = DEFAULT_ENV_FILE
            settings = load_runtime_settings(
                env_file, load_env_file=env_file is not None
            )
        cluster = SharedCluster.build(
            settings=settings,
            product_provider=getattr(catalog, "lookup", None),
            metrics_provider=getattr(catalog, "metrics", None),
            **cluster_kwargs,
        )
        store = TraceStore(trace_file if persist_traces else None)
        return cls(settings, cluster, store, catalog=catalog, post_node=post_node)

    # ------------------------------------------------------------------
    def attach_post_node(self, node: PostNode) -> None:
        """把业务后置节点挂到状态图上（只在图路径生效）。

        后置节点需要 cluster，而 cluster 属于底座，所以只能"底座先建好、业务再挂"。
        这也是为什么它是独立一步而不是 build() 的参数：业务 Agent 的种类在
        底座建好之后才知道。
        """
        self.graph = GraphAgent(self.cluster, store=self.store, post_node=node)

    # ------------------------------------------------------------------
    async def run(
        self,
        query: str,
        *,
        engine: str = "langgraph",
        session_id: str = "default",
        prompt_patch: str = "",
        policy: dict[str, Any] | None = None,
        record: bool = True,
    ) -> Trace:
        """按 `engine` 选一条路径执行。两条路径产出同一种 Trace。

        `policy` 只在 ReAct Loop 上生效：状态图的下一步由边决定，
        它没有"要不要再调工具"这个决策点，管不着。
        """
        if engine == "langgraph":
            return await self.graph.run(query, session_id=session_id, record=record)
        if engine == "react-loop":
            return await self.loop.run(
                query,
                session_id=session_id,
                prompt_patch=prompt_patch,
                policy=policy,
                record=record,
            )
        raise ValueError(f"未知引擎 {engine!r}，可选：{', '.join(ENGINES)}")

    # ------------------------------------------------------------------
    # 自进化
    # ------------------------------------------------------------------
    async def _replay_runner(self, engine: str, query: str, patch: PromptPatch | None) -> Trace:
        """回放用的单次运行：把补丁的 Prompt 文本与编排策略一起带上。"""
        return await self.run(
            query,
            engine=engine,
            session_id="replay",
            prompt_patch=patch.render() if patch else "",
            policy=patch.policy if patch else None,
            record=False,
        )

    async def replay(
        self, patch: PromptPatch, *, engine: str = "react-loop", limit: int | None = None
    ) -> ReplayReport:
        """回放失败用例，对比补丁前后的通过率。"""

        async def run_once(query: str, p: PromptPatch | None) -> Trace:
            return await self._replay_runner(engine, query, p)

        return await self.evolution.evaluate(run_once, patch, limit=limit)

    async def evolve(
        self, *, engine: str = "react-loop", limit: int | None = None
    ) -> dict[str, Any]:
        """一轮完整自进化：分析失败模式 → 逐条回放验证 → 汇总可采纳的补丁。"""

        async def run_once(query: str, p: PromptPatch | None) -> Trace:
            return await self._replay_runner(engine, query, p)

        return await self.evolution.iterate(run_once, limit=limit)

    # ------------------------------------------------------------------
    # 知识库
    # ------------------------------------------------------------------
    async def ingest(self, docs_dir: str | None = None, *, rebuild: bool = True):
        return await self.cluster.ingest_dir(
            docs_dir or self.settings.docs_dir, rebuild=rebuild
        )

    # ------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """底座状态快照，含两条引擎与轨迹统计 —— 前端面板直接渲染这个。"""
        snapshot = self.cluster.describe()
        snapshot.update(
            {
                "engines": list(ENGINES),
                "dirs": {
                    "docs": self.settings.docs_dir,
                    "vector": self.settings.vector_dir,
                    "traces": self.settings.trace_dir,
                },
                "limits": {"max_iterations": self.settings.max_iterations},
                "traces": self.store.stats(),
            }
        )
        return snapshot


__all__ = ["AgentRuntime", "ENGINES"]
