"""文档入库：扫描 data/docs/*.md，按文件名前缀映射业务线 source，向量化存入本地。"""
import glob
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DOCS_DIR
from core.rag_service import RAGService

# 文件名前缀 -> source 业务线
_SOURCE_MAP = {
    "01": "product", "02": "review", "03": "ad",
    "04": "logistics", "05": "listing",
}


def _detect_source(fname: str) -> str:
    for prefix, src in _SOURCE_MAP.items():
        if fname.startswith(prefix):
            return src
    return "default"


def main():
    files = sorted(glob.glob(os.path.join(DOCS_DIR, "*.md")))
    if not files:
        print(f"[ingest] 未找到文档：{DOCS_DIR}")
        return
    docs = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        fname = os.path.basename(f)
        docs.append({"id": fname, "text": text, "source": _detect_source(fname)})
    rag = RAGService()
    rag.build_from_docs(docs)
    cnt = Counter(d["source"] for d in docs)
    print(f"[ingest] 完成：{len(files)} 篇文档 -> {rag.count()} 个文本块")
    for k, v in cnt.items():
        print(f"  - source={k}: {v} 篇")


if __name__ == "__main__":
    main()
