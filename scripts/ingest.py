"""文档入库：把 `data/docs/<业务域>/*.md` 切分、向量化、写进向量库。

    python -m scripts.ingest

**业务域由目录决定，不由文件名前缀决定**。早期版本靠 `01_` `02_` 这类文件名前缀
映射业务域，结果是"改个文件名就换了域"，而且没人看得出来。目录即域之后，
检索按域过滤这件事才有稳定的依据。
"""
from __future__ import annotations

import asyncio
import os

from ecom_runtime import AgentRuntime
from ecom_runtime.mcp_server import build_runtime


async def main() -> None:
    runtime: AgentRuntime = build_runtime()
    docs_dir = runtime.settings.docs_dir

    if not os.path.isdir(docs_dir):
        print(f"[ingest] 目录不存在：{docs_dir}")
        return

    report = await runtime.ingest(docs_dir)
    payload = report.to_dict() if hasattr(report, "to_dict") else report
    print(f"[ingest] 完成：{payload}")
    print(f"[ingest] 切片总数：{runtime.cluster.rag.count()}")


if __name__ == "__main__":
    asyncio.run(main())
