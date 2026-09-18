"""业务 Agent 集合。

新增一个业务 Agent 的完整步骤：在这个目录加一个文件 → 在下面导出一行。
运行时的编排、模型、检索、记忆、工具协议都不需要改 ——
这是"底座把公共能力收走"之后应该得到的结果，如果新增 Agent 还要动底座，说明收得不干净。
"""
from .catalog import CATALOG, lookup, metrics
from .multimodal import build_materials
from .presale import PresaleAgent, make_materials_node

__all__ = [
    "CATALOG",
    "lookup",
    "metrics",
    "build_materials",
    "PresaleAgent",
    "make_materials_node",
]
