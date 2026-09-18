# 自进化 Agent 运行时底座

> 跨境电商 AI 应用作品集 · 生态的**底座层**
> 双引擎编排（自研 ReAct Loop + LangGraph）· 自进化闭环（轨迹 → 归因 → 补丁 → 回放）· MCP 工具服务
> 附带一个 **售前实时咨询 Agent** 验证底座能跑通真实业务链路

底座不自己实现模型接入、检索、记忆与工具协议 —— 那些在
[`ecom-agent-shared`](https://github.com/rchzc/ecom-agent-shared)（共享集群）里。
本仓库只做**编排、留痕与自进化**。

---

## 这个仓库解决什么问题

上层每写一个业务 Agent，就要重写一遍「怎么编排、怎么调工具、怎么处理失败」。
更麻烦的是**失败之后没人知道改的那句 Prompt 到底有没有用** —— 调完上线，问题还在。

本仓库把这两件事收敛成一套底座：

| 能力 | 做了什么 | 实现 |
| --- | --- | --- |
| **双引擎编排** | LangGraph 状态图（流程由代码决定）与自研 ReAct Loop（下一步由模型决定）并存，**产出同一种 Trace** | `ecom_runtime/graph.py`、`ecom_runtime/loop.py` |
| **自进化闭环** | 落轨迹 → 按失败模式归因 → 提补丁 → **回放同一批用例比通过率**，只有真的上升才采纳 | `ecom_runtime/evolution.py` |
| **策略注入** | `policy` 参数让自进化能把「同一工具失败到上限就禁用」这类**编排策略**注入循环 | `ecom_runtime/loop.py` |
| **意图路由** | 规则分类把问题判到 5 类业务域，决定检索范围（闭集短文本，不值得花一次模型调用） | `ecom_runtime/intent.py` |
| **MCP 服务** | 把注册的工具用标准 MCP 暴露，stdio / HTTP 两种传输共用同一套 JSON-RPC 处理 | `ecom_runtime/mcp_server.py` |
| **组装收敛** | `AgentRuntime.build()` 一个入口装齐全套，业务侧不再各自 new 一遍 | `ecom_runtime/runtime.py` |

**样例 Agent**：`ecom_runtime/agents/presale.py` 走完整链路 ——
用户问商品 → 意图路由 → 编排引擎 → 经 MCP 调 RAG / 商品 / 预约工具 → 生成回答 + 可投放素材草稿。

---

## 自进化闭环：这个仓库最该被追问的部分

Agent 的失败分两类，**它们的修法不一样**，混在一起谈就会一直修不好：

| 失败类型 | 例子 | 修得掉吗 |
| --- | --- | --- |
| **模型可自纠** | 臆造了不存在的工具名 | 能 —— 在 Prompt 里加一条约束 |
| **循环结构性** | 反复撞同一个工具报错 | **不能** —— 模型下一轮照样调，拦住它的是循环本身 |

所以补丁分两类：`rule`（Prompt 约束）与 `policy`（编排策略）。
只产出 Prompt 文字的自优化，遇到结构性失败会一直"报告已修复"却毫无变化。

`python -m scripts.evolve` 用**真的坏掉的工具**跑一遍（故障注入 `rag_search` 每次抛异常）：

```
故障注入：rag_search 每次都失败，先制造一批失败轨迹
  d29189b26d6f  fail  repeat_tool_error       迭代 6  阿迪达斯的广告 ACOS 太高了怎么降
  8c48994cf2b5  fail  repeat_tool_error       迭代 6  这个类目现在还能不能进
  0a57126104ce  fail  repeat_tool_error       迭代 6  差评率 4% 要怎么处理

分析出 1 条补丁建议：
  · patch-repeat_tool_error  证据 3 条  policy={'max_tool_errors_per_tool': 1}
    同一个工具连续失败两次后必须停止调用它，改为基于已知信息作答并说明信息缺口。

逐条回放验证（同一批查询，比补丁前后的通过率）
  [采纳] patch-repeat_tool_error: 0% -> 100% (0->3 / 3)
```

两个刻意的设计选择：

- **失败是"真的坏掉"造出来的，不是伪造几条失败轨迹。** 伪造的话，后面的"通过率提升"就没有意义了。
- **回放同一批查询，不是另造一批。** 换一批用例再比等于没有对照组。

---

## 依赖关系

```
ecom-agent-shared（共享集群 · 可 pip 安装）
        ↑
ecom-agent-runtime（本仓库：编排 / 留痕 / 自进化）
        ↑
ecommerce-ai-workbench（数据中台 + 业务 Agent + 交付层）
```

单向依赖，写在 `pyproject.toml` 与 `requirements.txt` 里而不是只写在文档里。
业务数据（商品、经营指标）通过**依赖注入**进来 —— `product_provider` 就是这条边界的实物，
共享层不认识任何业务模块。

架构图见 [`架构图_规范化.svg`](架构图_规范化.svg)。

---

## 目录结构

```
ecom-agent-runtime/
├── ecom_runtime/
│   ├── runtime.py           ★ 门面：AgentRuntime.build() 一次组装
│   ├── loop.py              ★ 自研 ReAct Loop（迭代计数 / 工具错误预算 / 轨迹留痕）
│   ├── graph.py             ★ LangGraph 状态图（intent → retrieve → generate → [post]）
│   ├── evolution.py         ★ 自进化：Trace / 失败归因 / 补丁 / 回放
│   ├── intent.py            意图路由（规则分类，5 类业务域）
│   ├── mcp_server.py        MCP 服务入口（stdio / HTTP / --selfcheck）
│   ├── config.py            RuntimeSettings（继承共享层 Settings，避免重复解析）
│   └── agents/              售前 Agent 样例：catalog / multimodal / presale
├── scripts/
│   ├── ingest.py            文档入库
│   ├── demo.py              端到端演示（--engine 选引擎）
│   └── evolve.py            自进化闭环演示（含故障注入）
├── tests/                   90 个测试，全部离线可跑
├── data/docs/<业务域>/*.md  知识库样例（目录即域，检索按域过滤）
├── pyproject.toml           依赖方向 + pytest 配置
├── requirements.txt         以 VCS 依赖安装共享集群
└── Dockerfile               非 root 运行 + HEALTHCHECK
```

---

## 快速开始（零成本，无需任何 key）

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements.txt

python -m scripts.demo --rebuild --offline          # 端到端演示
python -m scripts.demo --engine react-loop --offline  # 换另一条引擎
python -m scripts.evolve --offline                  # 自进化闭环（含故障注入）
python -m ecom_runtime.mcp_server --selfcheck --offline  # MCP 协议自检
python -m pytest -q                                 # 90 个测试
```

> `--offline` 把模型钉成 `mock`、检索钉成 `lexical`，不联网也不需要 Key。
> 离线模式下编排、检索、工具调用、失败归因、回放**全部真实执行**，只有推理文本是占位。

**为什么离线要显式加参数，而不是"没配 Key 就自动用 mock"**：
mock 是**显式 provider**，不是隐性降级。隐性降级会让人以为 Key 配好了其实没生效 ——
这是最难排查的一类问题。所以不给 Key 时的默认行为是**明确报错**并告诉你两条路：

```
ConfigError: 缺少 LLM_API_KEY。当前 provider=dashscope（阿里云百炼），
请填入对应厂商的 API Key，或改用 LLM_PROVIDER=mock 离线运行。
```

接入真实模型只需改 `.env` 里的 `LLM_PROVIDER` + `LLM_API_KEY`，业务代码零改动
（`api_base` 由厂商 preset 自动带出，要自建网关时才需要覆盖 `LLM_API_BASE`）：

| `LLM_PROVIDER` | 默认 `api_base` | 模型（轻 / 重） |
| --- | --- | --- |
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-reasoner` |
| `dashscope`（阿里云百炼） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-max` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` / `gpt-4o` |
| `ollama`（本地） | `http://localhost:11434/v1` | 按本地已拉取的模型填 `MODEL_LIGHT` / `MODEL_HEAVY` |

把工具暴露给本地 MCP 客户端：

```bash
python -m ecom_runtime.mcp_server --transport stdio   # stdout 是协议通道，日志走 stderr
python -m ecom_runtime.mcp_server --transport http    # 默认只绑 127.0.0.1（该端点无鉴权）
```

---

## 生态全景

| 层 | 仓库 | 内容 |
| --- | --- | --- |
| **① 底座层** | **本仓库** `ecom-agent-runtime` | 双引擎编排 · 自进化闭环 · MCP 工具服务 · 意图路由 |
| ② 共享集群 | [`ecom-agent-shared`](https://github.com/rchzc/ecom-agent-shared) | LLM 网关（四厂商统一接入）· RAG as a Service · 记忆 · Prompt 注册中心 · MCP 工具协议 · 137 个测试 |
| ③ 应用层 | [`ecommerce-ai-workbench`](https://github.com/rchzc/ecommerce-ai-workbench) | RAG 知识库与数据中台 · 业务 Agent 集群 · 单容器交付 |

依赖方向单向：③ → ① → ②。本仓库可独立 clone 运行。
