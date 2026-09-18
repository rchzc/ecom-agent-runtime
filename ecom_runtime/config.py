"""运行时配置：共享集群配置的超集。

**这里只做一件事：把共享包已经定义好的配置接过来，再补上本仓自己的两个字段。**

为什么不再自己读一遍 os.getenv：配置散落是这类项目最常见的腐化方式 —— 共享包改了
变量名（比如 `LLM_API_KEY` 换成厂商前缀），底座这份拷贝不会跟着变，于是同一份配置
出现两种解释。`RuntimeSettings` 继承 `Settings` 之后，共享包新增必填字段时这里
**不用改代码也不会漏**（构造走 `dataclasses.replace`），只有本仓真正独有的字段才写在下面。

本仓独有的字段都是"目录/路径"性质的，因为它们取决于这个仓库放在哪，
共享包不该知道宿主仓库的目录结构。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

from ecom_shared import ConfigError, Settings, load_settings

# 仓库根 = 本文件的上上级（ecom_runtime/config.py -> ecom_runtime -> 仓库根）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_FILE = os.path.join(REPO_ROOT, ".env")
DEFAULT_DOCS_DIR = os.path.join(REPO_ROOT, "data", "docs")
DEFAULT_TRACE_DIR = os.path.join(REPO_ROOT, "var", "traces")

#: 单次 Agent 循环的最大迭代数默认值。放在配置里而不是函数默认值，
#: 是因为它同时是"自进化"里一个失败模式的判据（撞上限 = 需要优化编排）。
MAX_ITERATIONS_DEFAULT = 6


@dataclass(frozen=True)
class RuntimeSettings(Settings):
    """共享配置 + 运行时底座自己的三个目录。

    继承而不是重新声明：`load_runtime_settings()` 只要把共享层校验过的字段
    原样搬过来即可，共享包以后加字段，这里自动跟上。
    """

    docs_dir: str = DEFAULT_DOCS_DIR
    trace_dir: str = DEFAULT_TRACE_DIR
    max_iterations: int = MAX_ITERATIONS_DEFAULT


def load_runtime_settings(
    env_file: str | None = DEFAULT_ENV_FILE, *, load_env_file: bool = True
) -> RuntimeSettings:
    """读共享配置 + 本仓字段，任何一项不合法直接抛 ConfigError。

    `load_env_file=False` 时只按当前进程环境变量解析，不再读 .env ——
    测试里需要确定性地按注入的变量取值，否则 .env 会把删掉的 Key 填回来。
    """
    base = load_settings(env_file, load_env_file=load_env_file)

    raw_iter = os.getenv("MAX_ITERATIONS", str(MAX_ITERATIONS_DEFAULT))
    try:
        max_iterations = int(raw_iter)
    except ValueError as exc:
        raise ConfigError(f"MAX_ITERATIONS={raw_iter!r} 不是合法整数") from exc
    if max_iterations < 1:
        raise ConfigError("MAX_ITERATIONS 必须 >= 1")

    return RuntimeSettings(
        **vars(replace(base)),
        docs_dir=os.getenv("DOCS_DIR") or DEFAULT_DOCS_DIR,
        trace_dir=os.getenv("TRACE_DIR") or DEFAULT_TRACE_DIR,
        max_iterations=max_iterations,
    )


__all__ = [
    "RuntimeSettings",
    "load_runtime_settings",
    "REPO_ROOT",
    "DEFAULT_ENV_FILE",
    "DEFAULT_DOCS_DIR",
    "DEFAULT_TRACE_DIR",
    "MAX_ITERATIONS_DEFAULT",
]
