"""自研 Agent Loop Engine：推理 → 工具调用 → 观察 → 反思（ReAct 范式）。

这是简历「项目一：商品交易自进化 Agent 运行时底座」的核心实现。
不依赖 LangChain，自己实现：
- 推理：LLM 决定下一步（直接回答 or 调工具）
- 工具调用：经 MCP 集群 call_tool 执行
- 观察：工具结果回灌上下文
- 反思：判断是否已满足，max_iterations 防死循环（自进化/自愈的关键护栏）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_gateway import chat
from mcp_server.server import call_tool, list_tools

_PROMPT_HEAD = (
    "你是一个售前咨询 Agent 的执行引擎。\n"
    "可用工具：\n"
)
_PROMPT_TAIL = (
    "\n\n根据用户问题，决定是直接回答，还是先调用工具获取信息。\n"
    "只输出一个 JSON，格式：\n"
    '{"action": "answer" 或 "tool", "content": "你的思考或最终回答", "tool": "工具名", "args": {"key": "value"}}\n'
    "说明：action=answer 时 content 是给用户的最终回答；action=tool 时 tool/args 指定要调用的工具。\n"
    "不要输出 JSON 以外的多余文字。"
)


def _build_system() -> str:
    tools_block = "\n".join(f"- {t['name']}: {t['description']}" for t in list_tools())
    return _PROMPT_HEAD + tools_block + _PROMPT_TAIL


def _parse_action(text: str) -> dict:
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end <= start:
            return {"action": "answer", "content": text}
        return json.loads(text[start:end])
    except Exception:
        return {"action": "answer", "content": text}


def run(user_query: str, session_history: list = None, max_iterations: int = 4) -> dict:
    """执行一轮 Agent Loop，返回 {answer, steps, tools_called}。"""
    messages = [{"role": "system", "content": _build_system()}]
    if session_history:
        messages.extend(session_history)
    messages.append({"role": "user", "content": user_query})

    steps = []
    tools_called = []
    for i in range(max_iterations):
        resp = chat(messages, model=None)
        action = _parse_action(resp)
        steps.append({"iter": i + 1, "action": action.get("action"), "tool": action.get("tool")})

        if action.get("action") == "answer":
            return {"answer": action.get("content", ""), "steps": steps, "tools_called": tools_called}

        tool_name = action.get("tool")
        tool_args = action.get("args") or {}
        tools_called.append(tool_name)
        obs = call_tool(tool_name, tool_args)
        messages.append({"role": "assistant", "content": resp})
        messages.append({"role": "user", "content":
                         f"工具 {tool_name} 返回：{json.dumps(obs, ensure_ascii=False)}。请继续。"})

    return {"answer": "（推理步数已达上限，建议拆分问题后重试）", "steps": steps, "tools_called": tools_called}


if __name__ == "__main__":
    r = run("推荐一款降噪耳机并查它的优惠")
    print("答案:", r["answer"])
    print("调用工具:", r["tools_called"])
    print("推理步数:", len(r["steps"]))
