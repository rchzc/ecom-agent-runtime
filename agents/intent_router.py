"""意图路由 Agent：判断用户问题归属业务线，用于 RAG source 过滤与分流。

规则分类（轻量、零推理开销）；如需更准可替换为 LLM 分类，接口不变。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INTENTS = {
    "product": "商品/选品咨询",
    "listing": "Listing/文案优化",
    "review": "评论/口碑",
    "ad": "广告/投放",
    "logistics": "物流/售后",
    "other": "其他",
}

_KEYWORDS = {
    "product": ["选品", "商品", "品类", "卖点", "推荐", "特点", "参数"],
    "listing": ["listing", "标题", "五点", "文案", "描述", "关键词"],
    "review": ["评论", "口碑", "差评", "好评", "评分", "反馈"],
    "ad": ["广告", "投放", "acos", "竞价", "预算", "roi"],
    "logistics": ["物流", "发货", "运费", "售后", "退货", "运单"],
}


def route(text: str) -> str:
    for intent, kws in _KEYWORDS.items():
        if any(k in text.lower() for k in kws):
            return intent
    return "other"


def route_label(text: str) -> str:
    return INTENTS.get(route(text), INTENTS["other"])


if __name__ == "__main__":
    print(route("这款耳机评论怎么样"), "→", route_label("这款耳机评论怎么样"))
    print(route("广告预算怎么分配"), "→", route_label("广告预算怎么分配"))
