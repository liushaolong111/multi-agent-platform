# 🤖 Multi-Agent Platform

> 基于 **LangGraph + RAG + 双层记忆** 构建的企业级多智能体协作平台

用户输入任务后，系统自动分类、拆解、检索知识库、分析、审核，最终给出**可溯源**的回答。

## ✨ 核心特性

- **五阶段工作流**：Router → Planner → Skill → Tool → Reviewer，每个节点职责单一、可独立调试
- **RAG 检索增强**：FAISS + bge-small-zh 向量检索，基于真实文档回答，减少幻觉
- **双层记忆**：Redis 短期记忆（会话上下文）+ PostgreSQL 长期记忆（跨会话历史）
- **流式输出**：SSE 逐节点推送，用户实时看到 AI 的思考过程
- **Docker 化部署**：一条命令启动全套服务，不依赖本地环境

## 🏗️ 架构设计

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
[planner]   {'plan': ['收集销售数据', '分析趋势', '生成报告'], 'status': 'planned'}
[skill]     {'current_task': '收集销售数据', 'retrieved_docs': '日期,产品,区域...'}
[tool]      {'tool_result': '总销售额 1,262,000 元...', 'status': 'tool_processed'}
[reviewer]  {'review_verdict': 'APPROVED', 'final_answer': '根据知识片段...'}
```

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
│   └── main.py                 # FastAPI 服务 + SSE 流式接口
├── graph/
│   ├── state.py                # AgentState 共享状态定义
│   ├── service.py              # LangGraph 工作流编排
│   └── nodes/
│       ├── router.py           # 任务分类
│       ├── planner.py          # 任务拆解
│       ├── skill.py            # RAG + LLM 分析
│       ├── tool.py             # 工具调用决策
│       └── reviewer.py         # 结果审核
├── memory/
│   ├── short_term.py           # Redis 短期记忆
│   └── long_term.py            # PostgreSQL 长期记忆
├── rag/
│   └── ingest.py               # 向量化入库脚本
├── data/                       # 知识库原始文件
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## 🎯 设计亮点

### 1. 为什么用多智能体而不是单一 Prompt

单一 Prompt 存在两个问题：**容易产生幻觉**（LLM 编造数据）、**难以调试**（出错不知道是哪个环节）。

拆成 5 个职责明确的节点后：
- 每个节点可以独立测试、优化、替换
- 出错时能精确定位
- 后续扩展新节点不影响现有流程

### 2. 如何防止 Agent 死循环

Reviewer 节点如果返回 `REVISE`，会触发 Planner 重新规划。为防止无限循环，在 `AgentState` 中定义了 `iteration` 字段，每次重规划 `+1`，超过 3 次强制结束。

### 3. 如何减少 LLM 幻觉

- **Prompt 约束**："如果资料中没有相关信息，请明确说明"
- **基于 RAG 检索的真实文档**回答，而不是让 LLM 自由发挥
- **Reviewer 二次审核**，拦截低质量回答

### 4. 记忆层设计

- **短期记忆（Redis）**：存储当前会话的上下文摘要，TTL 1 小时自动过期
- **长期记忆（PostgreSQL）**：存储用户历史交互，跨会话可召回

## 📊 项目规模

- 代码量：**约 800 行 Python**
- 工作流节点：**5 个**
- Docker 容器：**3 个**（API + Redis + PostgreSQL）
- 开发周期：**6 天从 0 到 1**

## 📄 License

MIT

## 👤 作者

- GitHub: [@liushaolong111](https://github.com/liushaolong111)