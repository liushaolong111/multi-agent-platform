# 🤖 Multi-Agent Platform

> 基于 **LangGraph + RAG + 双层记忆** 构建的企业级多智能体协作平台

用户输入任务后，系统自动分类、拆解、检索知识库、分析、审核，最终给出**可溯源**的回答。

## ✨ 核心特性

- **五阶段工作流**：Router → Planner → Skill → Tool → Reviewer，每个节点职责单一、可独立调试
- **RAG 检索增强**：FAISS + bge-small-zh 向量检索，基于真实文档回答，减少幻觉
- **双层记忆**：Redis 短期记忆（会话上下文）+ PostgreSQL 长期记忆（跨会话历史）
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
| 短期记忆 | **Redis** | 会话状态，TTL 自动过期 |
| 长期记忆 | **PostgreSQL** | 跨会话持久化存储 |
| 后端框架 | **FastAPI** | 异步高性能 HTTP 服务 |
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
> Q1 2026 总销售额为 **2,535,000 元**，Q4 2025 为 **2,328,000 元**，环比增长率 **+8.9%**。
>
> 贡献最大的区域-产品组合是**华北-星辰CRM**（370,000 元，占比 14.6%）。

**问**：客户满意度怎么样？最需要改进的是什么？

**答**：
> 客户平均评分 **3.85/5.0**，好评率 65%。得分最低的维度是**文档易用性**（3.3 分），其次是性能（3.5）和价格（3.8）。建议优先优化帮助文档和新手引导。

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

## 📁 项目结构

```
multi-agent-platform/
├── api/
│   └── main.py                 # FastAPI 服务 + SSE 流式接口 + Web UI
├── graph/
│   ├── llm.py                  # 共享 LLM 单例 + safe_invoke（重试+降级）
│   ├── state.py                # AgentState 共享状态定义
│   ├── service.py              # LangGraph 工作流编排
│   └── nodes/
│       ├── router.py           # 任务分类（带枚举校验）
│       ├── planner.py          # 任务拆解（JSON 解析兜底）
│       ├── skill.py            # RAG + 精确统计 + 遍历所有子任务
│       ├── tool.py             # 工具调用（query_database/query_feedback/read_file）
│       └── reviewer.py         # 结果审核 + 写入长期记忆
├── memory/
│   ├── short_term.py           # Redis 短期记忆
│   └── long_term.py            # PostgreSQL 长期记忆
├── rag/
│   └── ingest.py               # 向量化入库脚本
├── tests/
│   └── test_smoke.py           # pytest 冒烟测试（mock LLM）
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
# 运行冒烟测试（mock LLM，无需 API Key）
pytest tests/test_smoke.py -v
```

测试覆盖：Router 分类、Planner 拆解、Skill 多任务遍历、Tool 决策、Reviewer 审核、全链路端到端。

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

- **短期记忆（Redis）**：存储当前会话的上下文摘要，TTL 1 小时自动过期
- **长期记忆（PostgreSQL）**：审核通过的问答自动写入，跨会话可召回
- **优雅降级**：Redis / PostgreSQL 不可达时自动跳过，不阻塞主流程

### 5. 工程健壮性

- 统一 `config.py` 管理所有环境变量
- 共享 LLM 单例 + `safe_invoke`（指数退避重试 + 失败降级）
- `logging` 替换 `print`，全链路日志带节点标签
- Router 输出枚举校验、Planner JSON 解析兜底
- `pytest` 冒烟测试覆盖所有节点和全链路

## 📊 项目规模

- 代码量：**约 1000 行 Python**
- 工作流节点：**5 个**
- 测试用例：**6 个**（pytest）
- Docker 容器：**3 个**（API + Redis + PostgreSQL）
- 支持分析：环比增长、区域×产品交叉表、月度趋势、反馈量化统计

## 📄 License

MIT

## 👤 作者

- GitHub: [@liushaolong111](https://github.com/liushaolong111)