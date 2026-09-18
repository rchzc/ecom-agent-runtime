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

#: 离线运行要钉死的两个变量。
#:
#: `VECTOR_BACKEND=lexical` 也是必需的：默认值是 `chroma`，而 chromadb 是共享包的可选依赖
#: （`pip install ecom-agent-shared[chroma]`），默认不装。不钉这一项，"离线可跑"就不成立。
OFFLINE_ENV: dict[str, str] = {
    "LLM_PROVIDER": "mock",
    "VECTOR_BACKEND": "lexical",
}


def enable_offline() -> None:
    """把当前进程钉成离线模式：mock 模型 + lexical 检索，不联网、不需要任何 Key。

    **为什么不直接让"没有 Key 就自动用 mock"**：共享包刻意把 mock 做成**显式 provider**
    而不是隐性降级（见 `ecom_shared/gateway/mock.py` 的说明）—— "悄悄降级"会让人以为
    Key 配好了其实没生效，是最难排查的一类问题。所以默认行为保持 fail-fast，
    离线必须是**被明确要求**的：要么设这个环境变量，要么在命令行加 `--offline`。

    ⚠️ 已经踩过一次：README 一度写着"零成本、无需任何 Key"，但干净 clone 里没有 `.env`
    （`.env` 是 gitignore 的），provider 落到默认的 `dashscope` 又没 Key，
    于是 `scripts.demo` / `scripts.evolve` / `--selfcheck` **四个入口全部直接报错**，
    而 `pytest` 却能过 —— 因为测试自己在 conftest 里钉了 mock。
    「测试全绿」完全掩盖了「人跑不起来」。所有面向人的入口都必须能被零配置跑通。

    覆盖（而不是 `setdefault`）是刻意的：用户显式加了 `--offline`，
    就该压过 `.env` 里的真实厂商配置，否则这个开关在配好 Key 的机器上会静默失效。
    （`load_dotenv` 默认不覆盖已有环境变量，所以这里先设就先赢。）
    """
    for key, value in OFFLINE_ENV.items():
        os.environ[key] = value


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
    "enable_offline",
    "OFFLINE_ENV",
    "REPO_ROOT",
    "DEFAULT_ENV_FILE",
    "DEFAULT_DOCS_DIR",
    "DEFAULT_TRACE_DIR",
    "MAX_ITERATIONS_DEFAULT",
]
