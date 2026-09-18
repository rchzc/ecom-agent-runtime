"""意图路由：判断问题归属业务域，用于检索时的域过滤与分流。

规则分类而不是模型分类，是刻意的选择：

- 域判定是个**闭集上的短文本分类**，关键词命中率已经很高，没必要花一次模型调用；
- 它处在每次请求的**最前面**，一旦引入模型调用，整条链路就有两次模型延迟；
- 规则可解释、可单测，出问题能立刻定位到是哪条规则。

如果哪天要换成模型分类，只要保持 `route()` 的签名，上层不用改 ——
这也是把它单独放一个模块、而不是塞进 graph 节点里的原因。

域取值与共享集群的 `load_documents()` 目录约定一致（`<docs_dir>/<domain>/*.md`），
两边对齐之后，新增一个域就只是加一个目录 + 一组关键词。
"""
from __future__ import annotations

DOMAINS: dict[str, str] = {
    "selection": "商品 / 选品咨询",
    "listing": "Listing / 文案优化",
    "review": "评论 / 口碑",
    "ads": "广告 / 投放",
    "logistics": "物流 / 售后",
    "general": "其他",
}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "selection": ("选品", "商品", "品类", "卖点", "推荐", "特点", "参数", "哪个好"),
    "listing": ("listing", "标题", "五点", "文案", "描述", "关键词", "详情页"),
    "review": ("评论", "口碑", "差评", "好评", "评分", "反馈", "退货原因"),
    "ads": ("广告", "投放", "acos", "竞价", "预算", "roi", "曝光", "点击率"),
    "logistics": ("物流", "发货", "运费", "售后", "退货", "运单", "时效", "清关"),
}


def route(text: str) -> str:
    """返回业务域 key，未命中任何规则时返回 `general`。"""
    lowered = (text or "").lower()
    # 按关键词命中数取最高分，而不是"先匹配到就返回"：
    # 「这款耳机的广告投放和评论口碑怎么样」同时命中 ads 与 review，
    # 按命中数排序才稳定，否则结果取决于 dict 顺序（Python 3.7+ 是插入序，
    # 等于把分类结果绑定到关键词表的书写顺序上，改一行就会变）。
    best_domain, best_hits = "general", 0
    for domain, keywords in _KEYWORDS.items():
        hits = sum(1 for k in keywords if k in lowered)
        if hits > best_hits:
            best_domain, best_hits = domain, hits
    return best_domain


def route_label(text: str) -> str:
    """返回给用户看的中文标签。"""
    return DOMAINS.get(route(text), DOMAINS["general"])


#: 检索时表示"不限域"的取值。与共享集群 `rag_search` 的 domain 默认值一致。
ANY_DOMAIN = "*"


def search_domain(text: str) -> str:
    """把路由结果转成**能直接喂给检索**的域取值。

    为什么不能直接把 `route()` 的结果拿去过滤：`general` 是一个**路由概念**
    （"没归到任何具体域"），不是知识库里的一个目录。知识库底下只有
    selection/ads/review/... 这些目录，用 `general` 去过滤等于要求
    `metadata.domain == "general"`，一条都命中不了 ——
    结果是"没识别出域"反而比"不限域"检索得更差，方向刚好反了。
    所以这里在边界上做一次翻译：具体域原样传，general 翻成不限域。

    路由结果本身仍然记进轨迹（它是可观测的指标，不该被这个翻译抹掉）。
    """
    domain = route(text)
    return ANY_DOMAIN if domain == "general" else domain
