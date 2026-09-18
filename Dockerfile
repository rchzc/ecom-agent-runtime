FROM python:3.13-slim

WORKDIR /app

# git 是必需的：共享集群以 VCS 依赖形式安装（见 requirements.txt）
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 默认离线可跑（不需要任何 Key）；填了 LLM_PROVIDER + LLM_API_KEY 即切真模型
ENV LLM_PROVIDER=mock
ENV VECTOR_BACKEND=lexical

# 建索引：目录约定 <DOCS_DIR>/<业务域>/*.md
RUN python -m scripts.ingest

# 非 root 运行：容器里以 root 跑应用，一旦被利用就是宿主机级别的风险
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import ecom_runtime; print(ecom_runtime.__version__)" || exit 1

CMD ["python", "-m", "scripts.demo"]
