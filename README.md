# 🤖 Multi-Agent Platform

[![CI](https://github.com/liushaolong111/multi-agent-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/liushaolong111/multi-agent-platform/actions/workflows/ci.yml)

> 基于 **LangGraph + RAG + 双层记忆** 构建的企业级多智能体协作平台

用户输入任务后，系统自动分类、拆解、检索知识库、分析、审核，最终给出**可溯源**的回答。

## ✨ 核心特性

- **五阶段工作流**：Router → Planner → Skill → Tool → Reviewer，每个节点职责单一、可独立调试
- **RAG 检索增强**：FAISS + bge-small-zh 向量检索，基于真实文档回答，减少幻觉
- **双层记忆**：Redis 短期记忆（会话上下文）+ SQLAlchemy 长期记忆（PostgreSQL/SQLite 可配置，跨会话历史）
- **原生 Function Calling**：工具决策走 DeepSeek 原生工具调用，不再是"prompt 里拼 JSON 再解析"
- **可量化效果**：20 题评测集 + 自动化评测脚本（关键词评分 + LLM-as-judge），改动前后可回归对比
- **可观测性**：LangSmith 链路追踪（可选）+ `/metrics` 端点实时暴露 token 用量与各节点延迟
- **流式输出**：SSE 逐节点推送，用户实时看到 AI 的思考过程
- **Docker 化部署**：一条命令启动全套服务，不依赖本地环境
- **多维数据分析**：支持环比增长、区域×产品交叉表、月度趋势、客户反馈量化统计

## ️ 架构设计

```
                         用户输入
                            │
                            ▼
                   ┌────────────────┐
                   │    Router      │  任务分类
                   │ data_analysis  │
                   │ report_gen     │
                   │ knowledge_q    │
                   └────────────────┘
                            │
                            ▼
                   ┌────────────────┐
                   │   Planner      │  拆解为 3-5 个子任务
                   └────────────────┘
                            │
                            ▼
        ┌─────────────────────────────────────┐
        │              Skill                  │
        │  ┌──────────┐    ┌───────────────┐  │
        │  │  Redis   │    │     FAISS     │  │
        │  │ 短期记忆 │    │  RAG 检索     │  │
        │  └──────────┘    └───────────────┘  │
        │         ▼              ▼            │
        │      ┌──────────────────────┐       │
        │      │   DeepSeek LLM 推理  │       │
        │      └──────────────────────┘       │
        └─────────────────────────────────────┘
                            │
                            ▼
                   ┌────────────────┐
                   │     Tool       │  工具调用决策
                   └────────────────┘
                            │
                            ▼
                   ┌────────────────┐
                   │   Reviewer     │  审核 (APPROVED / REVISE)
                   └────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
        REVISE → 返回 Planner          APPROVED → 输出
        (最多循环 3 次)
```

## 🛠️ 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| 编排框架 | **LangGraph** | 多智能体工作流引擎 |
| LLM | **DeepSeek** | OpenAI 兼容接口，中文能力强 |
| Embedding | **BAAI/bge-small-zh-v1.5** | 本地运行，零 API 成本 |
| 向量检索 | **FAISS** | Facebook 开源，高效相似度搜索 |
| 短期记忆 | **Redis** | 会话状态，TTL 自动过期；不可达时降级进程内存储 |
| 长期记忆 | **SQLAlchemy** | 跨会话持久化，PostgreSQL/SQLite 通过 `DATABASE_URL` 一键切换 |
| 工具调用 | **原生 Function Calling** | DeepSeek `bind_tools`，结构化决策、免除 JSON 解析兜底 |
| 链路追踪 | **LangSmith**（可选） | 环境变量开启，全链路 span + token 消耗可视化 |
| 后端框架 | **FastAPI** | 异步高性能 HTTP 服务，内置限流与可选鉴权 |
| 流式输出 | **SSE** | Server-Sent Events 实时推送 |
| 前端 | 原生 HTML/JS | 打字机效果展示 |
| 容器化 | **Docker Compose** | 三容器一键部署 |

## 🚀 快速开始

### 前置条件

- Docker 20.10+
- Docker Compose 2.0+
- DeepSeek API Key（[申请地址](https://platform.deepseek.com)）

### 启动步骤

```bash
# 1. 克隆项目
git clone https://github.com/liushaolong111/multi-agent-platform.git
cd multi-agent-platform

# 2. 配置 API Key
echo "DEEPSEEK_API_KEY=sk-你的真实key" > .env

# 3. 启动服务
docker compose up -d --build

# 4. 访问 Web UI
# 浏览器打开 http://localhost:8000/ui
```

### 常用命令

```bash
docker compose up -d       # 启动
docker compose stop        # 停止（保留数据）
docker compose down        # 停止并删除容器
docker compose logs -f api # 实时查看日志
```

## 📸 效果展示

### Web UI 界面

![Web UI 演示](docs/screenshot.png)

访问 `/ui` 后，输入任务可以看到**实时的执行过程**：

```
[router]    {'task_type': 'data_analysis', 'status': 'routed'}
[planner]   {'plan': ['计算Q1总销售额和环比增长率', '按区域×产品找出贡献最大组合'], 'status': 'planned'}
[skill]     {'current_task': '计算Q1总销售额...', 'tool_result': 'Q1 2,535,000元，环比+8.9%...'}
[tool]      {'tool_result': '...', 'status': 'tool_processed'}
[reviewer]  {'review_verdict': 'APPROVED', 'final_answer': '...'}
```

### 实际回答示例

**问**：Q1 销售额环比 Q4 增长了多少？哪个区域哪款产品贡献最大？

**答**：
> Q1 2026 总销售额为 **10,604,477 元**，Q4 2025 为 **9,527,929 元**，环比增长率 **+11.3%**。
>
> 贡献最大的区域-产品组合是**华东-星辰财务**（1,891,250 元，占比 17.8%）。

**问**：客户满意度怎么样？最需要改进的是什么？

**答**：
> 客户平均评分 **3.64/5.0**，好评率 60%。得分最低的维度是**系统稳定性**（2.5 分，4 条反馈），其次是产品功能（3.0）。建议优先排查稳定性问题并建立故障复盘机制。

### API 接口

```bash
# 普通接口（一次性返回）
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "分析销售数据"}'

# 流式接口（SSE 实时推送）
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "分析销售数据"}'
```

API 文档：`http://localhost:8000/docs`

### 多轮对话演示（记忆层生效的证据）

同一个 `session_id` 连续提问，第二轮会带上第一轮的上下文：

```bash
# 第一轮
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "Q1 销售额最高的产品是哪个？", "session_id": "demo1"}'
# → 星辰财务（5,383,743 元，占比 50.8%）

# 第二轮（"它"指代第一轮结论，短期记忆提供上下文）
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "它在华东区域的销售额是多少？", "session_id": "demo1"}'
# → 星辰财务在华东区域销售额为 1,891,250 元
```

跨会话后，长期记忆（`DATABASE_URL` 指向的 PostgreSQL/SQLite）仍会保留审核通过的问答，新会话可召回。

## 📏 效果评测

"减少幻觉"不能只是口号。`evals/` 目录提供 20 题带标准答案的评测集（覆盖数据分析、客户反馈、知识问答三类），改动 prompt / 模型 / 检索参数前后跑一遍即可量化回归：

```bash
# 关键词评分（确定性规则，零额外成本）
python evals/run_eval.py

# 追加 LLM-as-judge 语义评分（覆盖关键词匹配不到的表述）
python evals/run_eval.py --judge

# 只跑前 5 题快速冒烟
python evals/run_eval.py --limit 5
```

输出示例：

```
总体正确率：18/20 = 90.0%
  - data_analysis: 9/10 = 90.0%
  - feedback: 4/5 = 80.0%
  - knowledge_query: 5/5 = 100.0%
报告已保存：evals/report.json
```

> 注：评测会真实调用 LLM，需要 `DEEPSEEK_API_KEY` 和已构建的 FAISS 索引，因此不进 CI。

## 🔭 可观测性

**链路追踪（LangSmith，可选）**：在 `.env` 中配置后，每个节点的完整调用链、prompt、token 消耗自动上报到 [smith.langchain.com](https://smith.langchain.com)，无需改任何代码：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_你的key
```

**运行指标（内置，零依赖）**：`GET /metrics` 实时返回进程内聚合的指标快照——

```json
{
  "requests": {"total": 42, "errors": 0},
  "llm": {"calls": 168, "total_tokens": 152300, "avg_latency_ms": 1240.5, "p95_latency_ms": 2810.0},
  "tool_calls": {"query_database": 12, "draw_chart": 3},
  "nodes": {"skill": {"count": 42, "avg_ms": 3102.4, "p95_ms": 5600.1}}
}
```

线上排查路径：`/metrics` 定位慢节点 → LangSmith 看该节点具体调用链 → 日志按节点标签过滤。

## 🔒 API 安全

- **限流**：`/chat`、`/chat/stream` 默认单 IP 10 次/分钟（`RATE_LIMIT_PER_MINUTE` 可调），超出返回 429
- **可选鉴权**：设置 `API_KEY` 后，上述接口要求请求头 `X-API-Key` 匹配，否则 401
- **已知风险说明**：用户输入会拼接进 prompt，当前未做 prompt injection 过滤；面向公网部署时应增加输入审计与输出内容安全过滤（已在 roadmap）

## 📁 项目结构

```
multi-agent-platform/
├── api/
│   └── main.py                 # FastAPI 服务 + SSE 流式接口 + Web UI + 限流/鉴权 + /metrics
├── graph/
│   ├── llm.py                  # 共享 LLM 单例 + safe_invoke（重试+降级+token统计）+ Function Calling 封装
│   ├── metrics.py              # 进程内指标聚合（token/延迟/工具调用），/metrics 数据源
│   ├── state.py                # AgentState 共享状态定义
│   ├── service.py              # LangGraph 工作流编排（含节点耗时采集）
│   └── nodes/
│       ├── router.py           # 任务分类（带枚举校验）
│       ├── planner.py          # 任务拆解（JSON 解析兜底）
│       ├── skill.py            # RAG + 精确统计 + 遍历所有子任务（向量库懒加载）
│       ├── tool.py             # 原生 Function Calling 工具决策 + 确定性图表触发
│       └── reviewer.py         # 结果审核 + 写入长期记忆
├── memory/
│   ├── short_term.py           # Redis 短期记忆（不可达时降级进程内存储）
│   └── long_term.py            # SQLAlchemy 长期记忆（PostgreSQL/SQLite 可配置）
├── rag/
│   └── ingest.py               # 向量化入库脚本
├── evals/
│   ├── dataset.jsonl           # 20 题评测集（带标准答案关键词）
│   └── run_eval.py             # 自动化评测脚本（关键词评分 + LLM-as-judge）
├── tests/
│   └── test_smoke.py           # pytest 冒烟测试（全 mock，CI 可跑）
├── .github/workflows/ci.yml    # CI：ruff lint + pytest
├── data/                       # 知识库（销售明细、产品、财务、反馈）
├── config.py                   # 统一配置管理
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## 🧪 测试

```bash
# 运行冒烟测试（全 mock：不需要 API Key / Redis / FAISS 索引，CI 直接可跑）
pytest tests/ -v
```

测试覆盖：Router 分类、Planner 拆解、Skill 多任务遍历、Tool 决策（无工具 + 图表关键词确定性触发）、Reviewer 审核、指标聚合、全链路端到端。

> 说明：节点模块通过 `from graph.llm import safe_invoke` 导入，因此测试 patch 的是各节点模块内的引用而非 `graph.llm` 本身——这是保证 mock 真正生效的关键。

## 🎯 设计亮点

### 1. 抗幻觉：精确数字由程序计算，LLM 只组织语言

LLM 擅长语言但不擅长算数。本项目用 **pandas 预先计算所有精确统计**（销售额、占比、环比、交叉表），以"【精确统计数据】"块注入 Prompt，明确要求 LLM 优先使用这些数字、不要自己算。从根源上避免"AI 算错数"的硬伤。

### 2. 为什么用多智能体而不是单一 Prompt

单一 Prompt 存在两个问题：**容易产生幻觉**（LLM 编造数据）、**难以调试**（出错不知道是哪个环节）。

拆成 5 个职责明确的节点后：
- 每个节点可以独立测试、优化、替换
- 出错时能精确定位（SSE 逐节点推送，一眼看到卡在哪）
- 后续扩展新节点不影响现有流程

### 3. 如何防止 Agent 死循环

Reviewer 节点如果返回 `REVISE`，会触发 Planner 重新规划。为防止无限循环，在 `AgentState` 中定义了 `iteration` 字段，每次重规划 `+1`，超过 3 次强制结束。

### 4. 记忆层设计

- **短期记忆（Redis）**：存储当前会话的上下文摘要，TTL 1 小时自动过期；Redis 不可达时降级为进程内存储，开箱即用
- **长期记忆（SQLAlchemy）**：审核通过的问答自动写入，跨会话可召回；`DATABASE_URL` 支持 PostgreSQL / SQLite，数据库不可达时自动跳过

### 5. 工具决策：为什么从"拼 JSON"改成原生 Function Calling

早期版本让 LLM 在 prompt 里输出 `{"tool": "xxx"}` 再用 `json.loads` 解析，存在三个问题：LLM 偶尔返回多余文本导致解析失败、工具增删时要维护两份描述（prompt 文案 + 代码）、参数类型没有约束。改成 DeepSeek 原生 Function Calling 后：工具用 `@tool` 装饰器定义即自动产生 JSON Schema、决策结果结构化（`response.tool_calls`）、解析失败率归零。同时保留"图表关键词 → 直接绘图"的确定性前置规则——**能确定性判断的不交给模型**，这是比模型更可靠的工程原则。

### 6. 工程健壮性

- 统一 `config.py` 管理所有环境变量
- 共享 LLM 单例 + `safe_invoke`（指数退避重试 + 失败降级 + token 用量记录）
- `logging` 替换 `print`，全链路日志带节点标签
- Router 输出枚举校验、Planner JSON 解析兜底
- `pytest` 全 mock 冒烟测试覆盖所有节点和全链路，CI 每次提交自动跑 ruff + pytest

## 📊 项目规模

- 代码量：**约 1500 行 Python**
- 工作流节点：**5 个**
- 测试用例：**8 个**（pytest 全 mock）+ **20 题**离线评测集
- Docker 容器：**3 个**（API + Redis + PostgreSQL）
- 支持分析：环比增长、区域×产品交叉表、月度趋势、反馈量化统计

## 📄 License

MIT

## 👤 作者

- GitHub: [@liushaolong111](https://github.com/liushaolong111)