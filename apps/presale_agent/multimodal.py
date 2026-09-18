"""多模态素材生成：根据咨询意图生成图片 prompt / 短视频脚本 / 直播话术。

体现「多模态内容生成」能力点：售前咨询不只返回文字，还能产出可投放素材。
真模型下用 LLM 一次生成结构化素材；mock 下返回模板占位。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.llm_gateway import MOCK_LLM, chat


def build_multimodal(query: str, answer: str) -> dict:
    """返回 {image_prompt, video_script, live_talk} 三种素材草稿。"""
    if MOCK_LLM:
        return _mock_multimodal(query)
    prompt = (
        f"你是跨境电商内容运营。基于用户咨询与客服回答，生成可投放素材。\n"
        f"用户咨询：{query}\n客服回答：{answer}\n\n"
        '输出 JSON：{"image_prompt":"商品主图/场景图英文描述词",'
        '"video_script":"15秒短视频分镜脚本","live_talk":"30秒直播话术"}。只输出 JSON。'
    )
    resp = chat([{"role": "user", "content": prompt}], temperature=0.8, max_tokens=600)
    try:
        s = resp.find("{"); e = resp.rfind("}") + 1
        return json.loads(resp[s:e])
    except Exception:
        return _mock_multimodal(query)


def _mock_multimodal(query: str) -> dict:
    return {
        "image_prompt": f"[mock] product photo of: {query[:30]}, white background, e-commerce style",
        "video_script": "[mock] 15s 分镜：1.痛点引入 2.产品展示 3.核心卖点 4.促销口播",
        "live_talk": "[mock] 家人们看这款，核心卖点 balabala，今天直播间专属价，赶紧上车！",
    }


if __name__ == "__main__":
    print(build_multimodal("降噪耳机", "推荐 A 款主动降噪"))
