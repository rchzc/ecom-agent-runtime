"""自研轻量 RAG 向量服务：入库 + 检索 + source 元数据过滤。

取舍说明（纯 Python 自研，不引 chromadb）：
- 文档切片后逐块向量化，存入本地 vector_store（numpy 持久化）
- 检索按余弦相似度排序，支持按 source 字段隔离业务线（提升跨业务准确性）
- 零外部服务依赖，作品集规模完全够用，且能讲清每步原理
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COLLECTION, VECTOR_DIR
from core.embedder import embed

_CHUNK_SIZE = 200
_CHUNK_OVERLAP = 40


class RAGService:
    def __init__(self, collection: str = COLLECTION):
        self.collection = collection
        self.dir = os.path.join(VECTOR_DIR, collection)
        os.makedirs(self.dir, exist_ok=True)
        self.texts = []    # 原文块
        self.vectors = []  # 向量
        self.metas = []    # 元数据 {source, doc_id}
        self._load()

    # ---------- 入库 ----------
    def _split(self, text: str):
        step = max(1, _CHUNK_SIZE - _CHUNK_OVERLAP)
        return [text[i:i + _CHUNK_SIZE] for i in range(0, len(text), step) if text[i:i + _CHUNK_SIZE].strip()]

    def ingest_doc(self, doc_id: str, text: str, source: str):
        for i, chunk in enumerate(self._split(text)):
            self.texts.append(chunk)
            self.vectors.append(embed(chunk))
            self.metas.append({"source": source, "doc_id": f"{doc_id}#{i}"})

    def build_from_docs(self, docs: list):
        """docs: list of {id, text, source}。清空后重建。"""
        self.texts, self.vectors, self.metas = [], [], []
        for d in docs:
            self.ingest_doc(d["id"], d["text"], d.get("source", "default"))
        self._save()

    # ---------- 检索 ----------
    def search(self, query: str, top_k: int = 3, source: str = None) -> list:
        if not self.vectors:
            return []
        q = np.array(embed(query), dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        mat = np.array(self.vectors, dtype=np.float32)
        sims = mat @ q  # 已 L2 归一化 → 点积即余弦
        idx = np.argsort(-sims)
        results = []
        for i in idx:
            i = int(i)
            meta = self.metas[i]
            if source and meta["source"] != source:
                continue
            results.append({"text": self.texts[i], "score": float(sims[i]), "meta": meta})
            if len(results) >= top_k:
                break
        return results

    def count(self) -> int:
        return len(self.texts)

    # ---------- 持久化 ----------
    def _path(self, name):
        return os.path.join(self.dir, f"{name}.npy")

    def _save(self):
        np.save(self._path("vectors"), np.array(self.vectors, dtype=np.float32))
        with open(os.path.join(self.dir, "texts.json"), "w", encoding="utf-8") as f:
            json.dump({"texts": self.texts, "metas": self.metas}, f, ensure_ascii=False)

    def _load(self):
        vp = self._path("vectors")
        if os.path.exists(vp):
            self.vectors = np.load(vp).tolist()
            with open(os.path.join(self.dir, "texts.json"), encoding="utf-8") as f:
                data = json.load(f)
            self.texts = data["texts"]
            self.metas = data["metas"]


if __name__ == "__main__":
    rag = RAGService()
    print(f"已加载 {rag.count()} 个文本块")
