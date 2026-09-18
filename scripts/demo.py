"""端到端演示：售前实时咨询 Agent 完整闭环（意图路由 → RAG → LLM → 多模态）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rag_service import RAGService
from apps.presale_agent.agent import consult


def main():
    if RAGService().count() == 0:
        print("[demo] 知识库为空，请先运行：python scripts/ingest.py")
        return

    print("=" * 64)
    print("电商生态 AI · 售前实时咨询 Agent 演示（真闭环）")
    print("=" * 64)

    samples = [
        "推荐一款适合运动的降噪耳机",
        "这款耳机的评论口碑怎么样",
        "它有什么优惠活动吗",
    ]
    for q in samples:
        print(f"\n>>> 用户：{q}")
        r = consult(q, session_id="demo")
        print(f"<<< 意图：{r['intent']} | 工具：{r['tools_called']}")
        print(f"<<< 回答：{r['answer']}")
        mm = r["multimodal"]
        if mm:
            print(f"<<< 多模态·图片prompt：{str(mm.get('image_prompt', ''))[:48]}...")
            print(f"<<< 多模态·视频脚本：{str(mm.get('video_script', ''))[:48]}...")


if __name__ == "__main__":
    main()
