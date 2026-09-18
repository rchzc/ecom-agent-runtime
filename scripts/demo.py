"""端到端演示：售前咨询 Agent 完整闭环（意图路由 → 检索 → 生成 → 素材草稿）。

    python -m scripts.demo              # 默认离线（.env 里 LLM_PROVIDER=mock）
    python -m scripts.demo --engine react-loop

演示的重点不是"回答得多好"，而是三条**能被追问的链路真的通了**：
1. 检索：命中知识库并带上来源；
2. 生成：走共享 Prompt 模板，输出被 Schema 约束；
3. 素材：一次咨询顺带产出可投放草稿。
每次运行都会在轨迹库里留一条记录，跑完打印轨迹统计。
"""
from __future__ import annotations

import argparse
import asyncio

from ecom_runtime.mcp_server import build_runtime

SAMPLES = [
    "推荐一款适合运动的降噪耳机",
    "这款耳机的评论口碑怎么样",
    "它有什么优惠活动吗",  # 依赖上一轮的指代解析
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default="langgraph", choices=("langgraph", "react-loop"))
    parser.add_argument("--rebuild", action="store_true", help="先重建索引再演示")
    args = parser.parse_args()

    # 延迟导入：这里是为了让脚本能单独跑，而不是把业务 Agent 编进底座依赖
    from ecom_runtime.agents import PresaleAgent

    runtime = build_runtime()
    agent = PresaleAgent.install(runtime)

    if args.rebuild or runtime.cluster.rag.count() == 0:
        report = await runtime.ingest()
        print(f"[demo] 已建索引：{report.to_dict() if hasattr(report, 'to_dict') else report}")

    snap = runtime.describe()
    print("=" * 72)
    print("跨境电商 AI · 售前实时咨询 Agent（运行时底座 + 共享集群）")
    print(
        f"provider={snap['provider']}({snap['provider_label']}) mock={snap['mock']} "
        f"| 检索后端={snap['retrieval'].get('backend', '?')} 切片={runtime.cluster.rag.count()}"
    )
    print(f"引擎={args.engine} | 工具={len(snap['tools'])} 个 | Prompt={len(snap['prompts'])} 个")
    print("=" * 72)

    for query in SAMPLES:
        result = await agent.consult(query, session_id="demo", engine=args.engine)
        print(f"\n>>> 用户：{query}")
        print(f"<<< 域：{result['domain']} | 工具：{result['tools_called']} | 迭代：{result['iterations']}")
        print(f"<<< 回答：{result['answer']}")
        materials = result.get("materials") or {}
        if materials:
            print(f"<<< 素材·图片 prompt：{str(materials.get('image_prompt', ''))[:56]}...")
            print(f"<<< 素材·直播话术：{str(materials.get('live_talk', ''))[:56]}...")

    print("\n" + "-" * 72)
    print("轨迹统计：", runtime.store.stats())


if __name__ == "__main__":
    asyncio.run(main())
