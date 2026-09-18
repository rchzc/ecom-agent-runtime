"""LLM Gateway：统一封装云端模型调用，按任务复杂度路由轻/重模型（成本优化）。

体现能力点：
- openai-compatible 统一接入（DeepSeek / 百炼 / OpenAI 仅 .env 差异）
- classify_complexity 粗判复杂度，简单任务走轻模型省 token
- 无 key 自动降级 mock，保证可演示
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_BASE, API_KEY, LIGHT_MODEL, HEAVY_MODEL, MOCK_LLM

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

_client = None
if OpenAI and API_KEY:
    _client = OpenAI(api_key=API_KEY, base_url=API_BASE)


def classify_complexity(text: str) -> str:
    """规则粗判任务复杂度：复杂任务走重量模型出质量，简单任务走轻量模型省成本。"""
    heavy_keywords = ["对比", "分析", "为什么", "原因", "方案", "策略", "规划",
                      "总结", "诊断", "建议", "设计", "怎么选", "区别", "优劣"]
    if any(k in text for k in heavy_keywords) or len(text) > 120:
        return "complex"
    return "simple"


def chat(messages, model=None, temperature=0.7, max_tokens=800) -> str:
    """调用云端聊天模型；未配置时返回 mock 占位。"""
    if MOCK_LLM or _client is None:
        return _mock_chat(messages)

    model = model or LIGHT_MODEL
    resp = _client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content


def route_chat(user_text: str, messages, temperature=0.7) -> str:
    """按复杂度自动选模型后调用。"""
    model = HEAVY_MODEL if classify_complexity(user_text) == "complex" else LIGHT_MODEL
    return chat(messages, model=model, temperature=temperature)


def _mock_chat(messages) -> str:
    """降级占位，按调用场景返回不同形态，保证 mock 下两类引擎都能跑通：

    - loop_engine 首次（system 含「可用工具」）：返回 tool 决策 JSON，触发 RAG 调用
    - loop_engine 观察阶段（user 含「工具...返回」）：返回 answer JSON
    - graph generate / 普通对话：直接返回文本占位答案
    填了 API_KEY 后由真实大模型接管，业务代码零改动。
    """
    last_user = messages[-1]["content"] if messages else ""
    sys_prompt = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""

    if "工具 " in last_user and "返回" in last_user:
        return '{"action":"answer","content":"[MOCK 回答] 已根据检索到的知识库片段生成解答（填入 API_KEY 后由真实大模型生成）。"}'
    if "可用工具" in sys_prompt:
        query = ""
        for m in reversed(messages):
            if m.get("role") == "user" and "工具 " not in m["content"]:
                query = m["content"]
                break
        return json.dumps({"action": "tool", "tool": "rag_search",
                           "args": {"query": query, "top_k": 3}}, ensure_ascii=False)
    text = last_user
    if "用户问题：" in text:  # graph 的 generate 节点把整段 prompt 当 user 消息传入
        text = text.split("用户问题：", 1)[1]
    return f"[MOCK 回答] 已收到你的问题：「{text[:40]}...」。填入 API_KEY 后由真实大模型生成（此句为降级占位）。"


if __name__ == "__main__":
    msgs = [{"role": "user", "content": "这款耳机有什么特点？"}]
    print(route_chat("这款耳机有什么特点？", msgs))
    print("复杂度:", classify_complexity("请对比三款耳机的性价比并给出购买建议"))
