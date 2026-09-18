"""自进化闭环：把「调 Prompt 靠感觉」换成「有证据的回归」。

这个模块回答一个具体问题：**Agent 跑失败之后，怎么知道改 Prompt 到底有没有用？**

三件事，按顺序：

1. **落轨迹** —— 每次运行记下查询、每一步决策、调了哪些工具、迭代几次、成功还是失败、
   失败属于哪一类。没有这一步，后面的复盘全靠回忆。
2. **从失败模式里提规则** —— 四类可机械识别的失败（选了不存在的工具、反复撞同一个
   工具报错、迭代到上限、输出无法解析为 JSON），每一类对应一条针对性的 prompt 补充，
   并且**带上证据轨迹号**。不是"我觉得应该加一句约束"，而是"这 3 条轨迹都栽在这里"。
3. **回放同一批失败用例，比修复前后的通过率** —— 补丁生效前后跑的是**同一批查询**，
   通过率变化才是"改动有没有用"的判据。换一批用例再比，等于没有对照组。

刻意不做的事：不自动改写 Prompt 文本。规则只产出**建议补丁 + 证据**，由人决定是否采纳。
自动改写看着更"智能"，但改坏了没人知道是哪一步引入的，反而把可复现性丢了。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Iterable

# ---------------------------------------------------------------------------
# 失败模式：全部是"能机器识别"的，不做主观判断
# ---------------------------------------------------------------------------
FAILURE_MODES: dict[str, str] = {
    "unknown_tool": "模型选了不存在的工具",
    "tool_error": "工具调用返回失败",
    "repeat_tool_error": "反复撞同一个工具报错（同一个工具失败 ≥2 次）",
    "iteration_exhausted": "迭代到上限仍未给出答案",
    "unparsable_output": "模型输出无法解析成决策 JSON",
    "empty_answer": "给出了空答案",
}

#: 每条规则对应的 Prompt 补充文本。刻意写成"可执行的约束"而不是"请更仔细一点" ——
#: 后者对模型没有可操作性，改了等于没改。
_PATCH_RULES: dict[str, str] = {
    "unknown_tool": (
        "只能调用「可用工具」列表中列出的工具，不要臆造工具名。"
        "如果不确定某个工具是否存在，直接用 action=answer 回答已知信息。"
    ),
    "tool_error": (
        "工具返回 isError 或 ok=false 时，不要重复提交同一个参数组合；"
        "改用其它信息源，或直接基于已有信息作答。"
    ),
    "repeat_tool_error": (
        "同一个工具连续失败两次后必须停止调用它，改为基于已知信息作答并说明信息缺口。"
    ),
    "iteration_exhausted": (
        "最多只做一次工具调用。已有足够信息就直接回答，不要为了『更完整』反复检索。"
    ),
    "unparsable_output": (
        "只输出一个 JSON 对象，不要输出解释性文字、不要用 Markdown 代码块包裹。"
    ),
    "empty_answer": (
        "action=answer 时 content 必须是非空的完整回答；信息不足也要说明已知部分与缺口。"
    ),
}


@dataclass
class Trace:
    """一次 Agent 运行的完整留痕。"""

    query: str
    engine: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    iterations: int = 0
    outcome: str = "ok"  # ok | fail
    failure_mode: str = ""
    answer: str = ""
    prompt_variant: str = "base"
    #: 业务侧产出的小块结构化结果（如素材草稿）。刻意只放"小"东西：
    #: 轨迹要长期累积并落 JSONL，把大块正文塞进来会让轨迹库迅速变成日志垃圾场。
    extra: dict[str, Any] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trace":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def classify_failure(
    *,
    steps: Iterable[dict[str, Any]],
    tools_called: Iterable[str],
    tool_errors: Iterable[str],
    parse_errors: int,
    iterations: int,
    max_iterations: int,
    answer: str,
) -> str:
    """判定失败模式。返回空串表示这次运行是成功的。

    **成功的判据只有一条：最终拿到了非空答案。**
    中间撞过工具报错、模型幻觉过工具名、甚至解析失败重试过 —— 只要最后答出来了，
    就不算失败。把"过程有噪声"记成失败会让失败率虚高，导致后面的补丁针对的是
    伪问题（这是做自进化最容易走偏的一步）。

    失败时再归因，从"最能定位根因"的证据往"最笼统"排。
    """
    steps = list(steps)
    errors = list(tool_errors)

    if (answer or "").strip():
        return ""

    if parse_errors:
        return "unparsable_output"

    errored = Counter(errors)
    if errored and max(errored.values()) >= 2:
        # 同一个工具失败两次以上 —— 模型在重复提交无效调用，不是工具偶然抖动
        return "repeat_tool_error"
    if any("unknown tool" in e for e in errors):
        return "unknown_tool"
    if errors:
        return "tool_error"
    if iterations >= max_iterations:
        return "iteration_exhausted"
    if not steps:
        return "empty_answer"
    return "empty_answer"


class TraceStore:
    """轨迹库。默认纯内存，传 path 则落 JSONL（追加写，重启不丢）。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._traces: list[Trace] = []
        if path and os.path.exists(path):
            self._load()

    # -- 写 --
    def add(self, trace: Trace) -> Trace:
        self._traces.append(trace)
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
        return trace

    def clear(self) -> None:
        self._traces.clear()
        if self.path and os.path.exists(self.path):
            os.remove(self.path)

    # -- 读 --
    def all(self) -> list[Trace]:
        return list(self._traces)

    def get(self, trace_id: str) -> Trace | None:
        for t in self._traces:
            if t.trace_id == trace_id:
                return t
        return None

    def failures(self) -> list[Trace]:
        return [t for t in self._traces if not t.ok]

    def by_mode(self) -> dict[str, list[Trace]]:
        grouped: dict[str, list[Trace]] = defaultdict(list)
        for t in self.failures():
            grouped[t.failure_mode or "unknown"].append(t)
        return dict(grouped)

    def stats(self) -> dict[str, Any]:
        total = len(self._traces)
        failed = len(self.failures())
        return {
            "total": total,
            "ok": total - failed,
            "fail": failed,
            "pass_rate": round((total - failed) / total, 3) if total else 1.0,
            "by_mode": {mode: len(items) for mode, items in self.by_mode().items()},
        }

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self._traces.append(Trace.from_dict(json.loads(line)))


#: 光靠 Prompt 补不了的失败模式，必须同时改**编排策略**。
#:
#: 这是做自进化最容易踩空的地方：模型"反复撞同一个工具报错"时，
#: 在 prompt 里加一句"不要重复调用"往往没用 —— 它下一轮还是照调，
#: 因为循环本身没有阻止它的机制。真正有效的是给循环加**错误预算**：
#: 同一个工具失败到上限后，直接禁用工具、强制它基于已有信息作答。
#:
#: 所以补丁分两类：`rule`（Prompt 约束，模型可自纠）+ `policy`（编排策略，模型不可自纠）。
#: 只产出 Prompt 文字的自优化，遇到结构性失败会一直"报告已修复"却毫无变化。
_PATCH_POLICIES: dict[str, dict[str, Any]] = {
    "repeat_tool_error": {"max_tool_errors_per_tool": 1},
    "tool_error": {"max_tool_errors_per_tool": 1},
    "iteration_exhausted": {"max_iterations": 2},
    "unparsable_output": {"json_only": True},
    "unknown_tool": {"validate_tool_name": True},
    "empty_answer": {},
}


@dataclass
class PromptPatch:
    """一条针对性的 Prompt 补充 + 配套编排策略 + 支撑它的证据轨迹号。"""

    mode: str
    rule: str
    evidence: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)

    @property
    def patch_id(self) -> str:
        return f"patch-{self.mode}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "mode": self.mode,
            "mode_label": FAILURE_MODES.get(self.mode, self.mode),
            "rule": self.rule,
            "policy": self.policy,
            "evidence": self.evidence,
            "evidence_count": len(self.evidence),
        }

    def render(self) -> str:
        """渲染成拼进 system prompt 的文本。"""
        return f"【补充约束·{FAILURE_MODES.get(self.mode, self.mode)}】{self.rule}"


def propose_patches(store: TraceStore) -> list[PromptPatch]:
    """扫轨迹库，按失败模式产出补丁建议（按证据条数从多到少排）。"""
    patches: list[PromptPatch] = []
    for mode, traces in store.by_mode().items():
        rule = _PATCH_RULES.get(mode)
        if not rule:
            continue
        patches.append(
            PromptPatch(
                mode=mode,
                rule=rule,
                evidence=[t.trace_id for t in traces],
                policy=dict(_PATCH_POLICIES.get(mode, {})),
            )
        )
    patches.sort(key=lambda p: len(p.evidence), reverse=True)
    return patches


@dataclass
class ReplayReport:
    """同一批用例在补丁前后的通过率对比。"""

    total: int
    before_pass: int
    after_pass: int
    fixed: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)
    patch_id: str = ""

    @property
    def before_rate(self) -> float:
        return round(self.before_pass / self.total, 3) if self.total else 1.0

    @property
    def after_rate(self) -> float:
        return round(self.after_pass / self.total, 3) if self.total else 1.0

    @property
    def effective(self) -> bool:
        """补丁是否值得采纳 —— 通过率必须**上升**才算有效。"""
        return self.after_rate > self.before_rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "total": self.total,
            "before_pass": self.before_pass,
            "after_pass": self.after_pass,
            "before_rate": self.before_rate,
            "after_rate": self.after_rate,
            "effective": self.effective,
            "fixed": self.fixed,
            "still_failing": self.still_failing,
        }


#: 回放时调用的一次运行。签名 (query, patch) -> Trace；patch 为 None 表示基线。
Runner = Callable[[str, "PromptPatch | None"], Awaitable[Trace]]


async def replay_failures(
    run_once: Runner,
    store: TraceStore,
    *,
    patch: PromptPatch | None = None,
    modes: Iterable[str] | None = None,
    limit: int | None = None,
) -> ReplayReport:
    """回放失败用例，对比补丁前后的通过率。

    回放的是**存下来的那批查询本身**，不是另造一批 —— 没有对照组就谈不上"有没有用"。
    """
    selected = store.failures()
    if modes is not None:
        wanted = set(modes)
        selected = [t for t in selected if t.failure_mode in wanted]
    if limit is not None:
        selected = selected[:limit]

    report = ReplayReport(
        total=len(selected),
        before_pass=sum(1 for t in selected if t.ok),  # 失败样本里通常为 0，保留以支持混合集
        after_pass=0,
        patch_id=patch.patch_id if patch else "",
    )

    for original in selected:
        replayed = await run_once(original.query, patch)
        replayed.prompt_variant = patch.patch_id if patch else "base"
        store.add(replayed)
        if replayed.ok:
            report.after_pass += 1
            report.fixed.append(original.trace_id)
        else:
            report.still_failing.append(original.trace_id)

    return report


class EvolutionEngine:
    """把「提补丁 → 回放验证」串起来的最小闭环。"""

    def __init__(self, store: TraceStore) -> None:
        self.store = store

    def analyze(self) -> list[PromptPatch]:
        return propose_patches(self.store)

    async def evaluate(
        self, run_once: Runner, patch: PromptPatch, *, limit: int | None = None
    ) -> ReplayReport:
        """先测补丁前的基线，再测补丁后，两次必须是同一批查询。"""
        return await replay_failures(run_once, self.store, patch=patch, limit=limit)

    async def iterate(self, run_once: Runner, *, limit: int | None = None) -> dict[str, Any]:
        """一轮完整自进化：分析 → 逐条验证 → 汇总。"""
        patches = self.analyze()
        reports = []
        for patch in patches:
            reports.append(
                (await self.evaluate(run_once, patch, limit=limit)).to_dict()
            )
        return {
            "before": self.store.stats(),
            "patches": [p.to_dict() for p in patches],
            "reports": reports,
            "adopted": [r["patch_id"] for r in reports if r["effective"]],
        }
