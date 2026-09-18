"""ecom-agent-runtime —— 自进化 Agent 运行时底座。

跨境电商 AI 生态的中间层：**往上给业务 Agent 提供两条编排路径与自进化闭环，
往下把模型、检索、记忆、工具协议交给共享集群。**

    ┌──────────────────────────────────────────────────────┐
    │ 业务 Agent：售前咨询 / 内容运营 / 销售考核 / 数据中台        │
    └───────────────────────┬──────────────────────────────┘
                            │ 依赖本包
    ┌───────────────────────▼──────────────────────────────┐
    │  ecom-agent-runtime（本包）                            │
    │  ├─ loop       自研 ReAct Loop（模型决定下一步）          │
    │  ├─ graph      LangGraph 状态图（代码决定下一步）          │
    │  ├─ evolution  轨迹库 / 失败回放 / Prompt 补丁验证         │
    │  └─ intent     业务域路由（规则，零推理开销）               │
    └───────────────────────┬──────────────────────────────┘
                            │ 依赖 ecom-agent-shared
    ┌───────────────────────▼──────────────────────────────┐
    │  ecom-agent-shared：网关 / RAG / 记忆 / Prompt / MCP     │
    └──────────────────────────────────────────────────────┘

依赖方向单向：本包认识共享集群，共享集群不认识本包（`pyproject.toml` 里写死）。

快速上手：

    from ecom_runtime import AgentRuntime, PresaleAgent

    runtime = AgentRuntime.build()                 # 读 .env；没有 key 就显式走 mock
    agent = PresaleAgent.install(runtime)          # 挂多模态节点 + 注册业务工具
    print(await agent.consult("推荐一款运动耳机"))

自进化闭环：

    report = await runtime.evolve()                # 分析失败 → 回放验证 → 给出可采纳补丁
    print(report["adopted"])                       # 只有通过率真的上升的补丁才会在这里
"""
from .config import RuntimeSettings, load_runtime_settings
from .evolution import (
    FAILURE_MODES,
    EvolutionEngine,
    PromptPatch,
    ReplayReport,
    Trace,
    TraceStore,
    classify_failure,
    propose_patches,
    replay_failures,
)
from .graph import GraphAgent, build_graph
from .intent import DOMAINS, route, route_label
from .loop import ReActLoop
from .runtime import ENGINES, AgentRuntime

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # 组装
    "AgentRuntime",
    "ENGINES",
    "RuntimeSettings",
    "load_runtime_settings",
    # 编排
    "ReActLoop",
    "GraphAgent",
    "build_graph",
    "route",
    "route_label",
    "DOMAINS",
    # 自进化
    "EvolutionEngine",
    "Trace",
    "TraceStore",
    "PromptPatch",
    "ReplayReport",
    "FAILURE_MODES",
    "classify_failure",
    "propose_patches",
    "replay_failures",
]
