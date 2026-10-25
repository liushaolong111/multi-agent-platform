# 🤖 Multi-Agent Platform

[![CI](https://github.com/liushaolong111/multi-agent-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/liushaolong111/multi-agent-platform/actions/workflows/ci.yml)

> 基于 **LangGraph + RAG + 双层记忆** 构建的企业级多智能体协作平台

用户输入任务后，系统自动分类、拆解、检索知识库、调用工具、分析、审核，最终给出**可溯源**的回答。

## ✨ 核心特性

- **ReAct 工具循环**：Router → Planner → Skill → ToolDecide ⇄ ToolExec → Reviewer。模型看到工具执行结果（Observation）后再决定下一轮，支持多步探索；达到轮数上限（`MAX_TOOL_ROUNDS`）自动熔断，防止无限循环
- **人工审批闸门（HITL）**：高危工具（查业务库）执行前经 LangGraph `interrupt()` 挂起，**人工批准才放行**，断点由 checkpointer 持久化，`POST /chat/approve` 从断点恢复
- **JWT 认证 + RBAC 行级隔离**：登录签发 token（HS256），`admin / sales / viewer` 三角色；销售只能看本区域数据，权限作为**贯穿所有数据出口的不变量**（SQL / 预计算统计 / 知识检索 / 工具 / 图表共用同一判定）
- **Text-to-SQL 真查库**：查询业务库由 LLM 生成 SQL——三层只读防护 + 执行前静态越权校验 + 报错自愈重试，能答的问题集合 = 整个 schema 能表达的所有查询
- **RAG 检索增强**：FAISS / Qdrant 双后端（工厂模式，Qdrant 不可达自动降级 FAISS），bge-small-zh 本地向量化，检索按 metadata 过滤
- **双层记忆**：Redis 短期记忆（会话上下文）+ SQLAlchemy 长期记忆（PostgreSQL/SQLite 可配置，跨会话历史）
- **原生 Function Calling**：工具决策走 DeepSeek `bind_tools`，结构化决策，免除 prompt 拼 JSON 再解析
- **结构化日志**：structlog + contextvars——请求入口绑定 `request_id / session_id / user_id`，整条链路（含第三方库）日志自动携带，`LOG_FORMAT=json` 可被日志系统直接采集
- **LLM 全链路异步化**：Skill 的多子任务 `asyncio.gather` 并发，延迟从 N×T 降到 ≈1×T
- **可量化效果 + CI 门禁**：20 题评测集（关键词评分 + LLM-as-judge），评估脚本支持 `--gate`，正确率低于阈值直接让 CI 失败
- **可观测性**：LangSmith 链路追踪（可选）+ `/metrics` 端点实时暴露 token 用量与各节点延迟
- **流式输出**：SSE 逐节点推送，用户实时看到 AI 的思考过程
- **Docker 化部署**：`docker compose` 一条命令启动 5 服务（api / redis / postgres / qdrant + seed 初始化）
- **多维数据分析**：支持环比增长、区域×产品交叉表、月度趋势、客户反馈量化统计

## 🏗️ 架构设计

```
                        用户输入（JWT 认证 → user_id / role / region）
                                  │
                                  ▼
                        ┌──────────────────┐
                        │      Router      │ 任务分类
                        └──────────────────┘
                                  │
                                  ▼
                        ┌──────────────────┐
                        │     Planner      │ 拆解为 3-5 个子任务
                        └──────────────────┘
                                  │
                                  ▼
          ┌────────────────────────────────────────────────┐
          │                    Skill                        │
          │  Redis 短期记忆 + RAG 检索（FAISS / Qdrant）     │
          │  + 预计算精确统计 / Text-to-SQL（按角色过滤）    │
          │  └──── asyncio.gather 并发调用 DeepSeek ────┘   │
          └────────────────────────────────────────────────┘
                                  │
                                  ▼
          ┌────────────────────────────────────────────────┐
          │       ReAct 循环（最多 MAX_TOOL_ROUNDS 轮）      │
          │                                                 │
          │   ┌────────────┐  有高危调用   ┌────────────┐   │
          │   │ ToolDecide │ ────────────▶ │  Approval  │   │
          │   │ 决策调哪些 │               │ interrupt  │   │
          │   │ 工具(FC)   │ ◀──┐          │ 人工审批   │   │
          │   └────────────┘    │          └────────────┘   │
          │        │  无调用/    │                │          │
          │        │  已收敛     │ 观测回喂        ▼          │
          │        │            │          ┌────────────┐   │
          │        │            └──────────│  ToolExec  │   │
          │        │                       │ 执行 → 观测 │   │
          │        │                       └────────────┘   │
          └────────┼────────────────────────────────────────┘
                   │
                   ▼
          ┌──────────────────┐
          │    Reviewer      │ 审核（APPROVED / REVISE）
          └──────────────────┘
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
  REVISE → 返回 Planner   APPROVED → 输出
  (最多循环 3 次)
```

> RBAC 体现在**每一个数据出口**：Text-to-SQL 的 SQL、预计算统计、RAG 检索的 metadata 过滤、工具返回、图表数据，
> 五处共用同一个 `scoped_region(role, region)` 判定——最弱的通道决定整体安全性。

## 🛠️ 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| 编排框架 | **LangGraph** | StateGraph 条件边 + `interrupt()` 断点 + checkpointer 持久化 |
| LLM | **DeepSeek** | OpenAI 兼容接口，中文能力强 |
| Embedding | **BAAI/bge-small-zh-v1.5** | 本地运行，零 API 成本 |
| 向量检索 | **FAISS / Qdrant** | 工厂模式双后端；Qdrant 原生 payload 过滤，不可达自动降级 FAISS |
| 短期记忆 | **Redis** | 会话状态，TTL 自动过期；不可达时降级进程内存储 |
| 长期记忆 | **SQLAlchemy** | 跨会话持久化，PostgreSQL/SQLite 通过 `DATABASE_URL` 一键切换 |
| 业务数据 | **PostgreSQL + Text-to-SQL** | LLM 生成 SQL 查真实业务表，三层只读防护 + 执行前静态越权校验 |
| 认证 / 权限 | **PyJWT + RBAC** | HS256 token；PBKDF2-HMAC-SHA256 密码哈希；三角色行级数据隔离 |
| 工具调用 | **原生 Function Calling** | DeepSeek `bind_tools`，结构化决策、免除 JSON 解析兜底 |
| 结构化日志 | **structlog** | 与 stdlib 桥接，contextvars 全链路追踪，`LOG_FORMAT=json` 可直接采集 |
| 链路追踪 | **LangSmith**（可选） | 环境变量开启，全链路 span + token 消耗可视化 |
| 后端框架 | **FastAPI** | 异步高性能 HTTP 服务，slowapi 限流 + 可选 API Key 鉴权 |
| 流式输出 | **SSE** | Server-Sent Events 实时推送 |
| 前端 | 原生 HTML/JS | 登录身份切换 + 逐节点执行过程 + Markdown 渲染 |
| 容器化 | **Docker Compose** | 5 服务（api / redis / postgres / qdrant + seed），healthcheck 编排启动顺序 |

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

# 3. 启动服务（api / redis / postgres / qdrant）
docker compose up -d --build

# 4. 首次初始化：灌业务库种子数据 + 构建向量索引
docker compose --profile setup up seed

# 5. 访问 Web UI
# 浏览器打开 http://localhost:8000/ui
```

### 演示账号

初始化脚本会创建三个账号，用于体验 RBAC 行级隔离（Web UI 登录栏提供一键填充）：

| 用户名 | 密码 | 角色 | 数据权限 |
|--------|------|------|---------|
| `admin` | `admin123` | 管理员 | 全部数据 |
| `sales_hd` | `sales123` | 销售（华东） | 仅华东区域数据 |
| `viewer` | `viewer123` | 只读 | 全部数据但只读 |

### 企业模式（可选）

默认 `AUTH_ENABLED=false`、`APPROVAL_MODE=false`，本地开发免登录。按需开启：

```bash
AUTH_ENABLED=true    # /chat、/chat/stream 需要 JWT（先 POST /auth/login 拿 token）
APPROVAL_MODE=true   # 高危工具（查业务库）执行前挂起，需 POST /chat/approve 人工放行
LOG_FORMAT=json      # 结构化日志（每行一个 JSON，含 request_id/session_id/user_id）
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

访问 `/ui` 后，先登录（或一键选择演示账号），再输入任务，可以看到**实时的执行过程**：

```
[路由]      任务分类：data_analysis
[规划]      拆解为 2 个子任务：1. 计算 Q1 总销售额与环比  2. 按区域×产品找出贡献最大组合
[分析]      已完成知识检索与分析（2 个子任务）
[工具决策]  决定调用：query_business_database
[人工审批]  已批准放行                ← APPROVAL_MODE=true 时出现（否则直接执行）
[工具执行]  第 1 轮：执行了 1 个工具调用
[工具决策]  无需额外工具调用（ReAct 收敛）
[审核通过]  Q1 2026 总销售额 ...
```

登录身份会决定数据可见范围：用 `sales_hd` 登录后问"各区域销售额"，答案只包含**华东**；换 `admin` 登录则可见全部四区域。

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
# 1. 登录（企业模式必做）→ 拿 JWT
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "sales_hd", "password": "sales123"}'
# → {"access_token": "eyJ...", "token_type": "bearer", "user": {"user_id":"sales_hd","role":"sales","region":"华东"}}

# 2. 普通接口（一次性返回）；带 token 时身份一律以 token 为准
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message": "分析销售数据", "session_id": "demo1"}'

# 3. 流式接口（SSE 实时推送）
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message": "分析销售数据", "session_id": "demo1"}'

# 4. 人工审批（APPROVAL_MODE=true 且上一请求返回 PENDING_APPROVAL 时）
curl -X POST http://localhost:8000/chat/approve \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"session_id": "demo1", "approved": true}'
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

# 回归门禁：正确率低于阈值时退出码 1（CI 门禁用）
python evals/run_eval.py --gate
```

输出示例：

```
总体正确率：18/20 = 90.0%
  - data_analysis: 9/10 = 90.0%
  - feedback: 4/5 = 80.0%
  - knowledge_query: 5/5 = 100.0%
报告已保存：evals/report.json
```

> 注：评测会真实调用 LLM，需要 `DEEPSEEK_API_KEY` 和已构建的索引，因此不进主 CI 流程。CI 中的 `eval-gate` job 仅在仓库配置 `EVAL_GATE_ENABLED=true` 且提供 API Key secret 时运行——**正确率低于阈值直接失败，阻止合并**。

## 🔭 可观测性

**链路追踪（LangSmith，可选）**：在 `.env` 中配置后，每个节点的完整调用链、prompt、token 消耗自动上报到 [smith.langchain.com](https://smith.langchain.com)，无需改任何代码：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_你的key
```

**结构化日志（内置）**：`LOG_FORMAT=json` 时每行一个 JSON。请求入口绑定 `request_id`，`/chat` 再绑 `session_id` / `user_id`——该请求链路里任何角落（图节点、工具、httpx）打出的日志都自动携带这三个字段，按 `request_id` 一搜即得完整时间线：

```json
{"event": "[Auth] 用户登录成功：sales_hd（sales）", "request_id": "42dfd091054f", "logger": "api.auth", "level": "info", "timestamp": "2026-10-15T09:45:13.817667"}
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

线上排查路径：`/metrics` 定位慢节点 → LangSmith 看该节点具体调用链 → 结构化日志按 `request_id` 还原完整链路。

## 🔒 安全设计

**认证（JWT）**：`POST /auth/login` 校验用户名密码（PBKDF2-HMAC-SHA256，恒定时间比较防时序攻击）后签发 HS256 token，payload 携带 `sub / role / region`；`AUTH_ENABLED=true` 时 `/chat`、`/chat/stream`、`/chat/approve` 必须带 `Authorization: Bearer <token>`。**只要带了合法 token，身份一律以 token 为准——请求体里自报的 `user_id` 无法伪造身份**。

**授权（RBAC 行级隔离）**：`admin / sales / viewer` 三角色，`sales` 只能看本区域数据。权限不是某个功能的属性，而是**贯穿所有数据出口的不变量**，五处共用同一个 `scoped_region(role, region)` 判定：

| # | 数据出口 | 隔离手段 |
|---|---------|---------|
| 1 | Text-to-SQL | prompt 层权限硬约束 + 执行前静态越权校验（`sql_within_scope`） |
| 2 | 预计算统计 | `compute_stats(region)` 只算本区域（含环比基数同步过滤） |
| 3 | 知识检索 | chunk `visibility = public / company` + `rag_scope_filter` metadata 过滤 |
| 4 | 工具调用 | `query_database` / `query_feedback` / `read_file` 出口按角色判定 |
| 5 | 图表生成 | `_draw_chart(region)` 只统计本区域，标题带区域 |

**高危操作人工审批（HITL）**：`APPROVAL_MODE=true` 时，含高危工具（`query_business_database`）的调用经 `interrupt()` 挂起，`POST /chat/approve` 从断点恢复；审批端点**校验会话归属——只有发起该会话的用户能审批**，避免"知道 session_id 就能放行高危操作"。

**限流与 API Key**：`/chat`、`/chat/stream` 默认单 IP 10 次/分钟（`RATE_LIMIT_PER_MINUTE` 可调），超出返回 429；设置 `API_KEY` 后另需请求头 `X-API-Key` 匹配。

**已知风险**：用户输入会拼接进 prompt，当前未做 prompt injection 过滤；面向公网部署时应增加输入审计与输出内容安全过滤。

## 📁 项目结构

```
multi-agent-platform/
├── main.py                     # 项目入口：uvicorn 启动 app.api.main:app
├── app/
│   ├── api/                    # 接入层
│   │   ├── main.py             # FastAPI 服务 + SSE 流式 + Web UI + /chat/approve + /metrics
│   │   └── auth.py             # JWT 登录签发 / 身份解析依赖（get_current_user）
│   ├── agent/                  # 智能体层
│   │   ├── service.py          # LangGraph ReAct 编排 + checkpointer
│   │   ├── state.py            # AgentState 共享状态定义
│   │   ├── llm.py              # 共享 LLM 单例 + safe_invoke / 异步版（重试+降级+token统计）
│   │   ├── metrics.py          # 进程内指标聚合（token/延迟/工具调用），/metrics 数据源
│   │   └── nodes/
│   │       ├── router.py       # 任务分类（带枚举校验）
│   │       ├── planner.py      # 任务拆解（JSON 解析兜底）
│   │       ├── skill.py        # RAG 检索 + 精确统计 + 子任务并发（asyncio.gather）
│   │       ├── tool_decide.py  # ReAct 决策：原生 Function Calling
│   │       ├── tool_exec.py    # ReAct 执行：执行工具 + 观测回喂
│   │       ├── approval.py     # HITL：interrupt 挂起 / resume 放行
│   │       ├── text2sql.py     # Text-to-SQL：生成 + 三层只读防护 + 越权校验 + 自愈重试
│   │       ├── tool.py         # 工具注册表（@tool：查库 / 反馈 / 读文件 / 图表）
│   │       └── reviewer.py     # 结果审核 + 写入长期记忆
│   ├── client/                 # 外部客户端
│   │   ├── short_term.py       # Redis 短期记忆（不可达时降级进程内存储）
│   │   └── vectorstore.py      # FAISS / Qdrant 工厂 + 统一过滤检索入口
│   ├── conf/
│   │   └── config.py           # 统一配置管理
│   ├── model/                  # 实体层（ORM）
│   │   ├── base.py             # declarative Base
│   │   ├── user.py             # 用户表 + 角色常量
│   │   └── business.py         # 销售流水 / 客户反馈表
│   ├── repository/             # 仓储层
│   │   ├── business.py         # 业务库连接 + schema + 只读 SQL 执行
│   │   ├── users.py            # 用户查询 / 密码哈希 / 登录认证
│   │   └── long_term.py        # SQLAlchemy 长期记忆（PostgreSQL/SQLite 可配置）
│   ├── service/                # 服务层
│   │   ├── permission.py       # RBAC 数据权限（scoped_region / sql_within_scope）
│   │   └── collect.py          # 外部数据源采集（工厂模式 + 增量缓存 + 失败降级）
│   ├── script/                 # 脚本层
│   │   ├── ingest.py           # 向量化入库脚本（含 visibility metadata）
│   │   ├── seed_business_db.py # 业务库种子脚本（含三个演示账号）
│   │   ├── sync_business_to_rag.py # 业务库 → 摘要 Markdown → 重建索引
│   │   └── demo_tool_permissions.py# 一键演示 RBAC 在各数据出口的效果
│   └── utils/
│       └── logging_config.py   # structlog 结构化日志配置
├── evals/
│   ├── dataset.jsonl           # 20 题评测集（带标准答案关键词）
│   └── run_eval.py             # 评测脚本（关键词 + LLM-as-judge + --gate 门禁）
├── tests/
│   ├── test_smoke.py           # 全链路 / 逐节点冒烟测试（全 mock）
│   ├── test_auth_rbac.py       # JWT / 密码哈希 / RBAC / 向量库降级
│   └── test_text2sql.py        # 只读防护 / SQL 提取 / 真执行
├── .github/workflows/ci.yml    # CI：ruff lint + pytest + 评测门禁
├── data/                       # 知识库（销售明细、产品、财务、反馈）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## 🧪 测试

```bash
# 全部测试（hermetic：不需要 API Key / Redis / FAISS 索引，CI 直接可跑）
pytest tests/ -q      # 29 passed
```

测试覆盖：**29 个测试**——Router 分类、Planner 拆解、Skill 子任务并发、ReAct 决策/执行/条件边路由、HITL 审批、Text-to-SQL 只读防护与越权拦截、JWT 签发/篡改/过期、密码哈希、RBAC 行级隔离（作用域原语 / 检索过滤 / 工具出口）、向量库工厂降级、指标聚合、全链路端到端。

> 说明：节点模块通过 `from app.agent.llm import safe_invoke` 导入，因此测试 patch 的是各节点模块内的引用（如 `app.agent.nodes.skill.safe_ainvoke`）而非 `app.agent.llm` 本身——这是保证 mock 真正生效的关键。

## 🎯 设计亮点

### 1. 抗幻觉：精确数字由程序计算，LLM 只组织语言

LLM 擅长语言但不擅长算数。本项目用 **pandas 预先计算所有精确统计**（销售额、占比、环比、交叉表），以"【精确统计数据】"块注入 Prompt，明确要求 LLM 优先使用这些数字、不要自己算。需要更灵活的口径时（写代码时想不到的查询），改走 **Text-to-SQL 直接查业务库**，由 SQL 引擎计算——**能由数据库算的不交给 LLM**。

### 2. 为什么用多智能体而不是单一 Prompt

单一 Prompt 存在两个问题：**容易产生幻觉**（LLM 编造数据）、**难以调试**（出错不知道是哪个环节）。

拆成 7 个职责明确的节点后：
- 每个节点可以独立测试、优化、替换
- 出错时能精确定位（SSE 逐节点推送，一眼看到卡在哪）
- 后续扩展新节点不影响现有流程

### 3. 如何防止 Agent 死循环

两道熔断：**重规划熔断**——Reviewer 返回 `REVISE` 会打回 Planner，`AgentState.iteration` 每次 `+1`，超过 `MAX_ITERATIONS`（3）强制结束；**工具循环熔断**——ReAct 的 `tool_rounds` 达到 `MAX_TOOL_ROUNDS`（3）时停止发起新调用并说明原因，避免模型在信息缺口上无限打转。

### 4. 记忆层设计

- **短期记忆（Redis）**：存储当前会话的上下文摘要，TTL 1 小时自动过期；Redis 不可达时降级为进程内存储，开箱即用
- **长期记忆（SQLAlchemy）**：审核通过的问答自动写入，跨会话可召回；`DATABASE_URL` 支持 PostgreSQL / SQLite，数据库不可达时自动跳过

### 5. 工具决策：为什么从"拼 JSON"改成原生 Function Calling

早期版本让 LLM 在 prompt 里输出 `{"tool": "xxx"}` 再用 `json.loads` 解析，存在三个问题：LLM 偶尔返回多余文本导致解析失败、工具增删时要维护两份描述（prompt 文案 + 代码）、参数类型没有约束。改成 DeepSeek 原生 Function Calling 后：工具用 `@tool` 装饰器定义即自动产生 JSON Schema、决策结果结构化（`response.tool_calls`）、解析失败率归零。同时保留"图表关键词 → 直接绘图"的确定性前置规则——**能确定性判断的不交给模型**，这是比模型更可靠的工程原则。

### 6. 工程健壮性：外部依赖永远假设它会挂

- 统一 `app/conf/config.py` 管理所有环境变量
- 共享 LLM 单例 + `safe_invoke` / 异步版（指数退避重试 + 失败降级 + token 用量记录）
- 同一条降级主线贯穿所有外部依赖：Redis 不可达 → 进程内存储；PostgreSQL 不可达 → 跳过长期记忆；Qdrant 不可达 → 降级 FAISS；外部数据源失败 → 跳过采集
- `logging` 替换 `print`；`LOG_FORMAT=json` 时 structlog 输出结构化日志，`request_id` 贯穿全链路
- Router 输出枚举校验、Planner JSON 解析兜底
- `pytest` 全 mock（29 个测试）覆盖所有节点与全链路；CI 每次提交自动跑 ruff + pytest

### 7. 为什么把"单轮工具调用"升级成 ReAct 循环

单轮 Function Calling 是"一次性点菜"：模型要完全部工具，执行完直接把结果丢给 Reviewer——**模型看不到工具执行结果**，遇到需要多步探索的问题（先查区域 → 发现华东异常 → 再钻取华东明细）就断了。改成 `tool_decide ⇄ tool_exec` 循环后，执行结果作为 Observation 回喂，模型能"尝一口再决定下一道"；再加 `MAX_TOOL_ROUNDS` 熔断兜底成本。

### 8. HITL：高危操作的人工闸门

企业场景里"AI 自动执行 SQL 查核心库"是运维红线。`approval` 节点用 LangGraph `interrupt()` 把图**可持久化地挂起**（状态序列化进 checkpointer），人审批多久都行，`Command(resume=...)` 从断点精确续跑——比自己在 Redis 里拼上下文可靠得多；拒绝时清空待执行调用并把拒绝原因回喂模型。

### 9. RBAC：权限是"贯穿所有数据出口的不变量"，不是某个功能的属性

第一版把权限只加在 Text-to-SQL 的 SQL 上，演示时被真实数据打脸——同一份全公司数据还有**预计算统计、知识检索、工具、图表**四个出口在裸奔。**最弱的通道决定整体安全性**。第二版抽出单一判定 `scoped_region(role, region)`，五个出口共用；检索层强制走带过滤的统一入口（**绕过入口 = 绕过权限**）。

## 📊 项目规模

- 代码量：**36 个 Python 文件、约 3500 行**
- 工作流节点：**7 个**（Router / Planner / Skill / ToolDecide / Approval / ToolExec / Reviewer）
- 测试用例：**29 个**（pytest 全 mock、hermetic）+ **20 题**离线评测集
- Docker 服务：**5 个**（api / redis / postgres / qdrant + seed 初始化）
- 支持分析：环比增长、区域×产品交叉表、月度趋势、反馈量化统计

## 📄 License

MIT

## 👤 作者

- GitHub: [@liushaolong111](https://github.com/liushaolong111)