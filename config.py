"""全局配置：从 .env 读取云端模型配置，支持 MOCK 自动降级。

设计要点（纯 Python 自研，不依赖 LangChain）：
- 统一走 openai-compatible 端点，DeepSeek / 阿里云百炼 / OpenAI 三家只需改 .env
- 未填 API_KEY 时自动降级 MOCK，保证代码任何时候都能跑通（面试本地演示不翻车）
- 填了 KEY 即为「可投入使用」的真实推理，无需改任何业务代码
"""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _bool(v, default=False):
    return (v or str(default)).lower() in ("1", "true", "yes", "y")


# ===== 云端兼容端点（openai-compatible）=====
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai-compatible")
API_BASE = os.getenv("API_BASE", "https://api.deepseek.com/v1")  # 默认 DeepSeek
API_KEY = os.getenv("API_KEY", "")

# 聊天模型：轻量（简单任务省成本）/ 重量（复杂任务出质量）
LIGHT_MODEL = os.getenv("LIGHT_MODEL", "deepseek-chat")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "deepseek-reasoner")

# 向量化模型（embedding）
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-v3")  # 阿里云百炼默认
EMBED_API_BASE = os.getenv("EMBED_API_BASE", "")  # 留空复用 API_BASE

# ===== 存储 =====
VECTOR_DIR = os.getenv("VECTOR_DIR", "./vector_store")
DOCS_DIR = os.getenv("DOCS_DIR", "./data/docs")
COLLECTION = os.getenv("COLLECTION", "presale_kb")

# ===== 双模开关（默认走真模型，无 key 自动降级）=====
MOCK_LLM = _bool(os.getenv("MOCK_LLM"), False)
MOCK_EMBED = _bool(os.getenv("MOCK_EMBED"), False)

# 无 key 且未显式开 mock → 自动降级，保证不崩
if not API_KEY and not MOCK_LLM:
    print("[config] 未检测到 API_KEY，自动降级 MOCK 模式（填入 .env 的 API_KEY 即切真模型）")
    MOCK_LLM = True
    MOCK_EMBED = True
