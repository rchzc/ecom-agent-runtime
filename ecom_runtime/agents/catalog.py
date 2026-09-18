"""业务数据源：商品目录。**放在业务仓，不放进共享包。**

这是"依赖方向单向"最具体的一处体现：

共享包只定义 `product_query` 这个工具的**契约**（参数叫什么、返回什么结构），
不知道也不该知道"商品数据长什么样、从哪来"。运行时由业务仓把实现注进去：

    SharedCluster.build(product_provider=catalog.lookup)

如果反过来让共享包 import 业务模块，共享包就再也没法单独安装、
也没法被第二个业务仓复用 —— 它会带上第一个业务的数据模型。

数据本身是演示用的内存字典；换成数据库/中台接口时只需要改 `lookup()` 的实现，
工具契约与上层 Agent 都不用动。
"""
from __future__ import annotations

from typing import Any

# 演示用目录：真实环境替换为商品库 / 平台 Open API
CATALOG: dict[str, dict[str, Any]] = {
    "小米 Buds 4 Pro": {
        "sku": "MI-BUDS4P",
        "price": 999.0,
        "stock": 128,
        "features": ["48dB 主动降噪", "双设备连接", "IP54 防水"],
        "promo": "今日下单立减 100，晒单返 30 元券",
    },
    "韶音 OpenFit Air": {
        "sku": "SH-OPENFITAIR",
        "price": 1098.0,
        "stock": 46,
        "features": ["开放式不入耳", "单次 10 小时续航", "适合运动"],
        "promo": "运动套装加 1 元换购耳挂",
    },
    "漫步者 NeoBuds Pro 2": {
        "sku": "ED-NBP2",
        "price": 599.0,
        "stock": 0,
        "features": ["圈铁双单元", "55dB 降噪", "LDAC 高清"],
        "promo": "缺货中，可预订 7 天后发货",
    },
}


def lookup(name: str) -> dict[str, Any]:
    """按名称/SKU/ASIN 查商品。命中不了时明确说"没找到"，不返回空壳。"""
    if not name:
        return {"found": False, "name": name, "reason": "商品名为空"}

    key = name.strip()
    item = CATALOG.get(key)
    if item is None:
        # 模糊匹配：用户往往只说「那款 OpenFit」，精确命中会漏
        for catalog_name, value in CATALOG.items():
            if key.lower() in catalog_name.lower() or key.upper() == value["sku"]:
                return {"found": True, "name": catalog_name, **value}
    if item is None:
        return {
            "found": False,
            "name": key,
            "reason": f"目录里没有 {key}",
            "available": list(CATALOG),
        }
    return {"found": True, "name": key, **item}


def metrics(seller_id: str, days: int = 7) -> dict[str, Any]:
    """经营指标（演示数据）。真实环境接数据中台。"""
    return {
        "seller_id": seller_id,
        "days": days,
        "available": True,
        "gmv": 128430.0,
        "orders": 862,
        "acos": 0.28,
        "return_rate": 0.041,
        "note": "演示数据；真实环境由数据中台提供",
    }
