FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# MOCK 模式可直接跑（无 key 也能演示）；填了 API_KEY 即切真模型
ENV MOCK_LLM=true
ENV MOCK_EMBED=true

RUN python scripts/ingest.py

CMD ["python", "scripts/demo.py"]
