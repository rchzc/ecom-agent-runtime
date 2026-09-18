"""向量化模块：云端 embedding API 为主，无 key 时本地确定性 hash 向量降级。

设计：自研轻量向量（不引 chromadb），RAG 检索靠 numpy 余弦相似度即可满足作品集规模。
真模型接入后 embedding 走云端，检索质量随模型提升，业务代码无需改动。
"""
import hashlib
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_BASE, API_KEY, EMBED_API_BASE, EMBED_MODEL, MOCK_EMBED

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

_embed_client = None
if OpenAI and API_KEY:
    _embed_client = OpenAI(api_key=API_KEY, base_url=EMBED_API_BASE or API_BASE)

_DIM = 256  # mock 向量维度


def embed(text: str) -> list:
    """返回归一化向量（list[float]）。"""
    if MOCK_EMBED or _embed_client is None:
        return _mock_embed(text)
    resp = _embed_client.embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding


def _mock_embed(text: str) -> list:
    """确定性向量：同一文本结果固定，保证 mock 下 RAG 检索稳定可复现。"""
    vec = np.zeros(_DIM, dtype=np.float32)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    for i in range(_DIM):
        vec[i] = (int(h[i % len(h)], 16) / 15.0) - 0.5
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


if __name__ == "__main__":
    v = embed("无线蓝牙耳机降噪")
    print(f"dim={len(v)}, norm={float(np.linalg.norm(v)):.3f}")
