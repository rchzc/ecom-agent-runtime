"""售前实时咨询 Agent：运行时底座上的一个具体业务 Agent。

**这个文件是"新增一个 Agent 只要加一个文件"的实物**。它没有自己的 LLM 客户端、
没有自己的检索实现、没有自己的会话记忆 —— 全部来自底座。它只做三件业务专属的事：

1. 定义自己的运行形态（沿用小图 + 挂一个多模态后置节点）；
2. 注册自己的工具（到店预约这类只有售前才需要的动作）；
3. 决定对外返回什么字段。

对照一下：如果每个 Agent 都自己 new 一遍网关和检索，换一次模型厂商就要改 6 个地方。
"""
from __future__ import annotations

from typing import Any

from ecom_shared import SharedCluster

from ..runtime import AgentRuntime
from .catalog import CATALOG, lookup
from .multimodal import build_materials


def make_materials_node(cluster: SharedCluster):
    """后置节点工厂：答完顺手产出素材草稿。

    做成工厂而不是直接写节点函数，是因为节点需要 cluster 而状态图不持有 cluster ——
    把依赖做成闭包，比往状态里塞一个 `_cluster` 字段干净（后者会让状态图不再是
    纯粹的数据结构，也没法序列化/断点续跑）。

    挂在图上而不是塞进 generate 节点里，是为了让"生成回答"和"生成素材"各自能被
    单独测试、也各自能被跳过（内容 Agent 只要素材不要回答时，直接调 multimodal）。
    """

    async def node(state: dict[str, Any]) -> dict[str, Any]:
        materials = await build_materials(cluster, state["query"], state.get("answer", ""))
        return {"extra": {**(state.get("extra") or {}), "materials": materials}}

    return node


class PresaleAgent:
    """售前咨询 Agent 门面。"""

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime

    # ------------------------------------------------------------------
    @classmethod
    def install(cls, runtime: AgentRuntime) -> "PresaleAgent":
        """把售前 Agent 装到底座上：挂后置节点 + 注册业务工具。

        **幂等**：重复调用返回同一个实例。装配点散落在脚本、MCP 入口、测试里，
        任何一处漏判"是不是已经装过"，都会撞上工具重名注册的报错 ——
        把幂等性做进 install 本身，比让每个调用方自己记得检查可靠。
        """
        existing = getattr(runtime, "_presale_agent", None)
        if existing is not None:
            return existing

        runtime.attach_post_node(make_materials_node(runtime.cluster))
        agent = cls(runtime)

        # 用 register_tool 而不是改共享包：共享包不该知道"到店预约"这件事，
        # 但注册进来的工具照样享受同一套协议、同一份调用留痕与 JSON-RPC 暴露。
        runtime.cluster.register_tool(
            "book_appointment",
            "为客户预约到店体验或 1v1 选品顾问，返回预约单号。",
            {
                "type": "object",
                "properties": {
                    "customer": {"type": "string", "description": "客户称呼或 ID"},
                    "slot": {"type": "string", "description": "期望时间段，如 2026-09-20 15:00"},
                },
                "required": ["customer"],
            },
            agent.book_appointment,
            owner="presale",
            side_effect=True,
        )
        runtime._presale_agent = agent  # type: ignore[attr-defined]
        return agent

    # ------------------------------------------------------------------
    def book_appointment(self, customer: str, slot: str = "待定") -> dict[str, Any]:
        """演示实现：真实环境写预约系统。"""
        return {
            "booked": True,
            "customer": customer,
            "slot": slot,
            "appointment_id": f"AP-{abs(hash((customer, slot))) % 100000:05d}",
        }

    # ------------------------------------------------------------------
    async def consult(
        self,
        query: str,
        *,
        session_id: str = "default",
        engine: str = "langgraph",
    ) -> dict[str, Any]:
        """一次售前咨询，返回回答 + 素材 + 引擎与轨迹信息。"""
        trace = await self.runtime.run(query, engine=engine, session_id=session_id)
        return {
            "engine": trace.engine,
            "answer": trace.answer,
            "materials": trace.extra.get("materials", {}),
            "domain": trace.extra.get("domain", ""),
            "tools_called": trace.tools_called,
            "iterations": trace.iterations,
            "outcome": trace.outcome,
            "failure_mode": trace.failure_mode,
            "trace_id": trace.trace_id,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def catalog_snapshot() -> dict[str, Any]:
        return {
            "count": len(CATALOG),
            "items": list(CATALOG),
            "sample": lookup("OpenFit"),
        }


__all__ = ["PresaleAgent", "make_materials_node"]
