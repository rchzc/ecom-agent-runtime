"""意图路由单测。

路由在链路最前面，一次错判会让后面所有环节都检索错域 ——
所以这里不只测"能否命中"，还测**同时命中多个域时的稳定性**。
"""
from __future__ import annotations

import pytest

from ecom_runtime.intent import ANY_DOMAIN, DOMAINS, route, route_label, search_domain


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("推荐一款适合运动的降噪耳机", "selection"),
        ("这个 listing 的标题怎么写", "listing"),
        ("差评太多怎么处理", "review"),
        ("广告预算怎么分配，ACOS 太高了", "ads"),
        ("发到美国多久能到，运费多少", "logistics"),
        ("今天天气不错", "general"),
    ],
)
def test_route_hits_expected_domain(text, expected):
    assert route(text) == expected


def test_route_is_case_insensitive_for_english_keywords():
    assert route("帮我看看这个 LISTING 的关键词") == "listing"


def test_route_picks_domain_with_most_hits_not_dict_order():
    """多域同时命中时按命中数取胜者，结果不依赖关键词表的书写顺序。"""
    text = "广告投放的预算怎么规划，ACOS 和 ROI 怎么算"  # 命中 ads 多个关键词
    assert route(text) == "ads"


def test_route_falls_back_to_general_without_raising():
    assert route("") == "general"
    assert route(None) == "general"  # type: ignore[arg-type]


def test_route_label_is_human_readable():
    assert route_label("广告预算") in DOMAINS.values()
    assert "广告" in route_label("广告预算")


def test_domains_cover_all_route_outputs():
    """每个域都要有中文标签，否则前端会显示成 key。"""
    for domain in DOMAINS:
        assert DOMAINS[domain]


def test_search_domain_passes_concrete_domain_through():
    assert search_domain("广告预算怎么分配") == "ads"


def test_search_domain_translates_general_to_unrestricted():
    """`general` 是路由概念、不是知识库目录。

    直传下去等于要求 metadata.domain == "general"，知识库里没有这个目录，
    结果是零命中 —— "没识别出域"反而比"不限域"检索得更差，方向刚好反了。
    """
    assert route("今天天气不错") == "general"
    assert search_domain("今天天气不错") == ANY_DOMAIN
    assert ANY_DOMAIN == "*"


def test_search_domain_never_returns_general():
    """兜住回归：任何输入都不该把 general 泄漏到检索层。"""
    for text in ("", "今天天气不错", None, "广告 ACOS"):
        assert search_domain(text) != "general"  # type: ignore[arg-type]
