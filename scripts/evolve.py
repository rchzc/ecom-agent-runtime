"""自进化演示：制造一批失败 → 分析失败模式 → 提补丁 → 回放验证补丁有没有用。

    python -m scripts.evolve

演示刻意用**真的坏掉**的工具（`rag_search` 每次调用都抛异常）来制造失败，
而不是伪造几条失败轨迹 —— 伪造的话，后面"通过率提升"就没有意义了。

跑完能看到三样东西：
1. 失败被归到哪一类（这里应该都是"反复撞同一个工具报错"）；
2. 补丁带了哪些证据轨迹号；
3. 同一批查询在补丁前后的通过率对比 —— **只有真的上升，补丁才会进 adopted**。

顺带说明一个容易被忽略的点：这个失败光加一句 Prompt 是修不掉的。
模型在被要求"不要重复调用"之后照样会调，因为拦住它的是**循环的错误预算**，不是提示词。
所以补丁分两类，`rule`（Prompt 约束）和 `policy`（编排策略），
自进化必须能同时产出这两类，否则遇到结构性失败只会一直"报告已修复"。
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from ecom_shared import setup_logging

from ecom_runtime.mcp_server import build_runtime

QUERIES = [
    "阿迪达斯的广告 ACOS 太高了怎么降",
    "这个类目现在还能不能进",
    "差评率 4% 要怎么处理",
]


def _break_rag(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("知识库服务不可用（演示用的故障注入）")


async def main() -> None:
    parser = argparse.ArgumentParser(description="自进化闭环演示")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印 INFO 级结构化日志（可以看到每一次工具调用的失败明细）",
    )
    args = parser.parse_args()

    runtime = build_runtime()
    # 默认压到 WARNING：故障注入会制造几十条同构的工具失败日志，
    # INFO 级下它们会把下面这套叙事整个埋掉。要证据时再开 --verbose。
    setup_logging("INFO" if args.verbose else "WARNING")
    # 依赖注入式地替换掉检索工具：不碰共享包，也不碰上层代码
    runtime.cluster.tools.unregister("rag_search")
    runtime.cluster.tools.register(
        "rag_search",
        "检索知识库（演示已故障注入）",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        _break_rag,
        owner="demo",
    )

    print("=" * 72)
    print("故障注入：rag_search 每次都失败，先制造一批失败轨迹")
    print("=" * 72)

    for query in QUERIES:
        trace = await runtime.run(query, engine="react-loop", session_id="evolve-demo")
        print(
            f"  {trace.trace_id}  {trace.outcome:4s}  {trace.failure_mode or '-':22s}  "
            f"迭代 {trace.iterations}  {query}"
        )

    print("\n轨迹统计：", runtime.store.stats())

    patches = runtime.evolution.analyze()
    print(f"\n分析出 {len(patches)} 条补丁建议：")
    for patch in patches:
        print(f"  · {patch.patch_id}  证据 {len(patch.evidence)} 条  policy={patch.policy}")
        print(f"    {patch.rule}")

    if not patches:
        print("没有可修的模式，结束。")
        return

    print("\n" + "-" * 72)
    print("逐条回放验证（同一批查询，比补丁前后的通过率）")
    print("-" * 72)
    reports = []
    for patch in patches:
        report = await runtime.replay(patch)
        reports.append(report)
        mark = "采纳" if report.effective else "丢弃"
        print(
            f"  [{mark}] {report.patch_id}: "
            f"{report.before_rate:.0%} -> {report.after_rate:.0%} "
            f"({report.before_pass}->{report.after_pass} / {report.total})"
        )
        print(f"        修复：{len(report.fixed)} 条  仍失败：{len(report.still_failing)} 条")

    # 别把 store 的累计通过率当成"修复后效果"：那里面还压着修复前故意造的那批失败样本，
    # 数字会停在 50% 左右，看起来像"一半还是坏的"。真正的效果看上面回放那一行。
    stats = runtime.store.stats()
    print(
        f"\n轨迹库累计：{stats['total']} 条"
        f"（成功 {stats['ok']} / 失败 {stats['fail']}，含修复前那批失败样本）"
    )
    last = reports[-1] if reports else None
    if last:
        print(f"补丁 {last.patch_id} 修复后通过率：{last.after_rate:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
