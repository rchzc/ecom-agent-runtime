"""自研 ReAct Loop：推理 → 工具调用 → 观察 → 反思。

**它和 LangGraph 那版（graph.py）解决的不是同一个问题**，这也是两版都要留的原因：

- LangGraph 版是"生产怎么写"：状态机显式声明节点与边，可加条件分支、可 human-in-the-loop、
  节点能单独测。业务流程确定的场景用它。
- 自研 Loop 版是"底层是什么"：决策与循环完全在自己手里，能在循环里插
  **迭代计数、工具错误预算、失败分类、轨迹留痕** —— 而这些恰是"自进化"要用的原料。
  LangGraph 也能记，但得在节点里挂回调，反而绕。

所以这里不做"自研 vs 框架"的取舍，而是把 Loop 定位成**可观测性最好、也最可干预的那条路径**：
`policy` 参数允许自进化把「同一个工具失败到上限就禁用工具」这类**编排策略**注入进来。
Prompt 只能约束模型，约束不了循环本身 —— 结构性失败必须靠策略修。

四个边界条件都显式处理（这类实现最容易漏的就是它们）：
1. 工具失败不抛异常 —— 失败是**观察值**，交回模型重新决策，而不是把整轮打断；
2. 迭代上限 —— 防工具互相触发导致的死循环；
3. 决策解析失败 —— 走共享包的三级容错解析；纯文本回复视为正常作答，不误判成故障；
4. 模型幻觉出不存在的工具 —— 注册表会返回可用工具清单，模型下一轮能自我纠正。
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ecom_shared import SharedCluster, parse_json_lenient

from .config import MAX_ITERATIONS_DEFAULT
from .evolution import Trace, TraceStore, classify_failure
from .intent import route, search_domain

_PROMPT_HEAD = "你是跨境电商 AI 助手的执行引擎。\n可用工具：\n"

_PROMPT_TAIL = (
    "\n\n根据用户问题，决定是直接回答，还是先调用工具获取信息。\n"
    "只输出一个 JSON，格式：\n"
    '{"action": "answer" 或 "tool", "content": "思考或最终回答", '
    '"tool": "工具名", "args": {"key": "value"}}\n'
    "说明：action=answer 时 content 是给用户的最终回答；"
    "action=tool 时 tool/args 指定要调用的工具。\n"
    "不要输出 JSON 以外的任何文字。"
)

#: 工具被禁用后强制收尾用的提示。不带"可用工具"字样是**功能性的**：
#: 离线 mock 网关靠这个关键词判断该不该发起工具调用，带上它就会永远调不到收尾分支。
_FINAL_ANSWER_PROMPT = (
    "本轮不允许再调用任何工具，请直接基于已知信息作答。\n"
    "只输出 JSON：" '{"action": "answer", "content": "回答正文"}'
)


class ReActLoop:
    """一轮 ReAct 循环的执行器。"""

    def __init__(
        self,
        cluster: SharedCluster,
        *,
        store: TraceStore | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self.cluster = cluster
        self.store = store
        # 优先用调用方传的（RuntimeSettings.max_iterations），
        # 其次看集群配置里有没有这个字段，最后落到共享层的默认值。
        self.max_iterations = (
            max_iterations
            or getattr(cluster.settings, "max_iterations", None)
            or MAX_ITERATIONS_DEFAULT
        )

    # ------------------------------------------------------------------
    def build_system_prompt(self, prompt_patch: str = "", *, declare_tools: bool = True) -> str:
        """工具清单直接来自 MCP 注册表 —— prompt 与真实可调用的工具永远一致。

        手写工具清单迟早会漂移：注册表加了工具而 prompt 忘了写，模型就不知道能用；
        反过来更糟 —— prompt 写了但没注册，模型一调就报"工具不存在"。
        """
        if not declare_tools:
            return _FINAL_ANSWER_PROMPT + (f"\n\n{prompt_patch}" if prompt_patch else "")

        lines = []
        for spec in self.cluster.tools.list_tools():
            params = ", ".join((spec.get("inputSchema") or {}).get("properties", {}))
            lines.append(f"- {spec['name']}({params}): {spec['description']}")
        prompt = _PROMPT_HEAD + "\n".join(lines) + _PROMPT_TAIL
        if prompt_patch:
            prompt += f"\n\n{prompt_patch}"
        return prompt

    # ------------------------------------------------------------------
    async def run(
        self,
        query: str,
        *,
        session_id: str = "default",
        prompt_patch: str = "",
        policy: dict[str, Any] | None = None,
        record: bool = True,
    ) -> Trace:
        """跑一轮循环，返回轨迹（含成败与失败模式）。

        `policy` 是自进化注入的编排策略，当前支持：
        - `max_iterations`：本轮迭代上限（"撞上限"这类失败靠缩短循环来止损）
        - `max_tool_errors_per_tool`：同一个工具失败到几次后禁用工具、强制收尾
        """
        policy = policy or {}
        max_iterations = int(policy.get("max_iterations") or self.max_iterations)
        error_budget = policy.get("max_tool_errors_per_tool")

        # 意图路由：走状态图那条路径时它由 intent 节点承担，Loop 这条没有节点，
        # 必须在入口补上。不补的后果有两层 —— 表层是两条引擎产出的 Trace 字段不一致
        # （extra 里有 domain 那条有、这条没有），深层是检索**真的**少了一道域过滤，
        # 同一批文档两条路径召回的东西不一样，"两条路径产出同一种 Trace"就成了空话。
        domain = route(query)
        routed_domain = search_domain(query)

        history = self.cluster.session_memory.messages(session_id)
        user = query
        if history:
            # 会话历史拼进 user 侧，让「它有什么优惠」这类指代能解析到上轮商品
            hist_text = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:])
            user = f"对话历史：\n{hist_text}\n\n用户问题：{query}"

        steps: list[dict[str, Any]] = []
        tools_called: list[str] = []
        tool_errors: list[str] = []
        error_counts: Counter[str] = Counter()
        parse_errors = 0
        answer = ""
        tools_forbidden = False

        for i in range(max_iterations):
            system = self.build_system_prompt(
                prompt_patch, declare_tools=not tools_forbidden
            )
            content, meta = await self.cluster.gateway.complete(system, user, json_mode=True)

            decision = parse_json_lenient(content)
            if not isinstance(decision, dict) or "action" not in decision:
                text = (content or "").strip()
                if len(text) >= 8:
                    # 模型直接用自然语言把答案说完了 —— 很常见，收下即可。
                    # 只有"既不是合法决策、又拿不到可用文本"才算解析失败。
                    answer = text
                    steps.append({"iter": i + 1, "action": "answer", "raw": True})
                    break
                parse_errors += 1
                steps.append({"iter": i + 1, "action": "parse_error"})
                user = (
                    "上一次输出无法解析为决策 JSON。请只输出一个 JSON 对象，"
                    "不要包含任何解释文字或 Markdown 代码块。\n\n"
                    f"（原始问题：{query}）"
                )
                continue

            action = decision.get("action")
            steps.append(
                {
                    "iter": i + 1,
                    "action": action,
                    "tool": decision.get("tool"),
                    "model": meta.get("model"),
                    "tier": meta.get("tier"),
                    "tools_forbidden": tools_forbidden,
                }
            )

            if action != "tool":
                answer = str(decision.get("content") or "").strip()
                break

            tool_name = decision.get("tool") or ""

            # 错误预算：同一个工具已经失败够了就不要再试。
            # 这是"策略补丁"真正起作用的地方 —— 光在 prompt 里写"不要重复调用"，
            # 模型下一轮照样会调，因为循环没拦住它。
            if error_budget is not None and error_counts[tool_name] >= int(error_budget):
                tools_forbidden = True
                steps.append(
                    {
                        "iter": i + 1,
                        "action": "tool_skipped",
                        "tool": tool_name,
                        "reason": f"错误预算已用尽（{error_counts[tool_name]} 次）",
                    }
                )
                user = f"工具 {tool_name} 已不可用。请直接基于已知信息作答。"
                continue

            tools_called.append(tool_name)
            args = dict(decision.get("args") or {})
            # 模型没指定域时补上路由结果 —— 但**不覆盖**模型自己指定的：
            # 模型可能比关键词规则更懂这次该查哪个域（规则只看字面命中）。
            # 编排层负责"兜底"，不负责"越权"。
            if tool_name == "rag_search" and not args.get("domain"):
                args["domain"] = routed_domain
            result = await self.cluster.tools.call(tool_name, args)
            if not result.get("ok"):
                tool_errors.append(str(result.get("error", "unknown")))
                error_counts[tool_name] += 1

            # 观察值回灌。措辞固定成「工具 X 返回：」，既是给模型的信号，
            # 也是离线 mock 网关判断"该收尾了"的依据。
            observation = result.get("result", result)
            user = (
                f"工具 {tool_name} 返回：{_dumps(observation)}。请继续。\n\n"
                f"（原始问题：{query}）"
            )

        trace = Trace(
            query=query,
            engine="react-loop",
            steps=steps,
            tools_called=tools_called,
            iterations=len(steps),
            answer=answer,
            prompt_variant="base",
            extra={
                "domain": domain,
                "tools_forbidden": tools_forbidden,
                "policy": policy,
            },
        )
        trace.failure_mode = classify_failure(
            steps=steps,
            tools_called=tools_called,
            tool_errors=tool_errors,
            parse_errors=parse_errors,
            iterations=len(steps),
            max_iterations=max_iterations,
            answer=answer,
        )
        trace.outcome = "fail" if trace.failure_mode else "ok"

        # 会话记忆：只记成功轮次。失败轮次记进去会污染下一轮的指代解析 ——
        # 用户下一句说"它"，指向的可能是上一轮那个根本没答上来的问题。
        if session_id and trace.ok:
            self.cluster.session_memory.append(session_id, "user", query)
            self.cluster.session_memory.append(session_id, "assistant", answer)

        if record and self.store is not None:
            self.store.add(trace)
        return trace


def _dumps(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    # 观察值过长会挤掉 prompt 里真正有用的部分，截断并把原长告诉模型
    return text if len(text) <= 1200 else text[:1200] + f"...(已截断，共 {len(text)} 字)"
