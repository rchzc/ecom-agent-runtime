"""多模态素材草稿：由一次咨询回答衍生出可投放的素材。

**它为什么挂在运行时底座里，而不是 ⑤ 内容运营 Agent 里**：
生成素材这件事本身与"卖什么"无关，它需要的只是一次结构化生成 + 一份 Schema。
放在底座里，售前 Agent 答完顺手就能出素材，内容 Agent 复用同一个函数，
不用两份 prompt 各写一遍（写两遍的结局通常是两边的字数约束不一致）。

结构化输出走 JSON Schema 约束：字段与取值范围写进 prompt，
输出能被下游直接消费（素材中心按商品归档），而不是一段需要人再整理的散文。
"""
from __future__ import annotations

from typing import Any

from ecom_shared import SharedCluster, parse_json_lenient

_SCHEMA_HINT = (
    '只输出 JSON：{"image_prompt": "商品图英文描述词", '
    '"video_script": "15 秒短视频分镜", "live_talk": "30 秒直播话术", '
    '"hashtags": ["话题标签"]}'
)

_SYSTEM = "你是跨境电商内容运营，负责把商品卖点改写成可投放的素材草稿。\n" + _SCHEMA_HINT


async def build_materials(cluster: SharedCluster, query: str, answer: str) -> dict[str, Any]:
    """基于一次咨询生成素材草稿。模型不可用或输出不合法时给确定性占位。"""
    user = f"用户咨询：{query}\n客服回答：{answer}\n请生成可投放素材。"
    content, meta = await cluster.gateway.complete(_SYSTEM, user, temperature=0.8)
    parsed = parse_json_lenient(content)
    if isinstance(parsed, dict) and parsed.get("image_prompt"):
        parsed.setdefault("hashtags", [])
        parsed["source"] = "model"
        parsed["model"] = meta.get("model")
        return parsed

    fallback = _fallback(query)
    fallback["source"] = "fallback"
    return fallback


def _fallback(query: str) -> dict[str, Any]:
    short = (query or "商品")[:30]
    return {
        "image_prompt": f"product photography of {short}, clean white background, soft light, e-commerce listing style",
        "video_script": "15s 分镜：1.痛点（3s）2.产品特写（4s）3.核心卖点（5s）4.促销口播（3s）",
        "live_talk": "家人们看这款，主打的卖点我先说清楚，今天直播间下单还有专属价。",
        "hashtags": ["#跨境电商", "#选品"],
    }
