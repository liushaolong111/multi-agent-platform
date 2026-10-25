# 项目目录与文件说明（新手向）

> 这份文档逐个解释项目里每个目录、每个文件是干什么的。
> 阅读建议：先看「一、整体鸟瞰」和「四、一次请求的完整流转」建立全局认识，再回头查具体文件。

---

## 一、整体鸟瞰

这是一个「多智能体数据分析平台」，用户用自然语言提问（如"分析上季度销售数据"），系统经过 Router → Planner → Skill → ReAct 工具循环 → Reviewer 协作后给出带精确数字的回答。代码按职责分层：

```
用户/浏览器
   │  HTTP / SSE
   ▼
app/api/        ← 接入层（FastAPI）：接收请求、推送流式结果、提供网页
   │
   ▼
app/agent/      ← 智能体编排层（LangGraph）：节点串成工作流
   ├── nodes/router.py      分类
   ├── nodes/planner.py     拆任务
   ├── nodes/skill.py       RAG 检索 + pandas 精确统计
   ├── nodes/tool_decide.py ReAct 决策（Function Calling）
   ├── nodes/tool_exec.py   ReAct 执行
   ├── nodes/approval.py    高危操作人工审批
   ├── nodes/reviewer.py    审核
   └── nodes/text2sql.py    Text-to-SQL
   │
   ├── app/client/      Redis 短期记忆 + 向量库客户端
   ├── app/repository/  数据库连接与查询（业务库 / 用户 / 长期记忆）
   ├── app/model/       ORM 实体
   ├── app/service/     RBAC 数据权限 + 外部数据采集
   └── data/            真实业务数据（CSV/Markdown）
```

---

## 二、根目录文件（配置与入口）

| 文件 | 类型 | 作用 |
|------|------|------|
| `main.py` | 源码 | **项目启动入口**。`python main.py` 即启动服务，监听 8000 端口 |
| `requirements.txt` | 文本 | Python 依赖清单及版本，`pip install -r requirements.txt` 安装 |
| `.env.example` | 文本 | 环境变量模板（**可以提交**，不含真实密钥）。复制为 `.env` 后填入真实 Key |
| `.env` | 文本（本地私有） | 真实配置，含 API Key，**已被 gitignore，绝不能提交** |
| `.gitignore` | 文本 | 声明哪些文件不进 Git（`.env`、`.venv`、缓存、数据库文件等） |
| `.dockerignore` | 文本 | 构建 Docker 镜像时排除哪些文件（类似 .gitignore） |
| `ruff.toml` | 文本 | 代码检查工具 ruff 的规则配置（CI 会用它检查代码风格） |
| `Dockerfile` | 文本 | 描述如何把本项目打包成 Docker 镜像 |
| `docker-compose.yml` | 文本 | 一键编排 API + Redis + PostgreSQL + Qdrant + 初始化容器 |
| `start.bat` | 文本 | Windows 一键启动本地 Redis 与服务 |
| `README.md` | 文本 | 项目门面文档（架构、启动方式、API、评测、截图） |
| `LICENSE` | 文本 | MIT 开源协议 |

---

## 三、各目录详解

### 📁 app/api/ —— 接入层

| 文件 | 作用 |
|------|------|
| `app/api/main.py` | **唯一的后端入口**，FastAPI 应用全部在这里：<br>• `POST /chat` 一次性回答接口（带限流、可选 API Key 鉴权）<br>• `POST /chat/stream` SSE 流式接口，逐节点推送"AI 思考过程"<br>• `POST /chat/approve` 人工审批恢复<br>• `GET /ui` 内嵌的网页（一大段 HTML/CSS/JS 写在这个文件里）<br>• `GET /health` 健康检查、`GET /metrics` 运行指标 |
| `app/api/auth.py` | JWT 签发与 `get_current_user` 依赖：身份以 token 为准，请求体 user_id 无法伪造 |

### 📁 app/agent/ —— 智能体编排层（项目核心）

| 文件 | 作用 |
|------|------|
| `app/agent/state.py` | 定义 `AgentState`——节点之间传递的**共享数据结构**（用户输入、任务类型、计划、工具结果、审核结论等） |
| `app/agent/service.py` | **工作流装配图**：定义节点顺序与条件跳转（ReAct 循环、审核不通过打回 planner，最多 3 次）；同时给每个节点包了耗时统计 |
| `app/agent/llm.py` | LLM 客户端单例 + 安全调用函数：`safe_invoke`（指数退避重试、失败兜底、token 记账）、`safe_ainvoke_with_tools`（原生 Function Calling） |
| `app/agent/metrics.py` | 进程内指标聚合器：请求数、LLM 调用、token 用量、节点延迟、工具调用次数，供 `/metrics` 读取 |
| `app/agent/nodes/router.py` | **节点1 路由分类器**：判断问题属于数据分析 / 报告生成 / 知识问答哪一类，输出有枚举校验 |
| `app/agent/nodes/planner.py` | **节点2 规划器**：把复杂问题拆成 2-4 个具体可执行的子任务（JSON 列表，带解析兜底） |
| `app/agent/nodes/skill.py` | **节点3 技能执行器（干活主力）**：遍历每个子任务——① FAISS/Qdrant 向量检索知识库片段；② 用 pandas 从 CSV **预计算精确统计数字**；③ 并发调用 LLM 组织语言 |
| `app/agent/nodes/tool_decide.py` | **ReAct 决策**：用 DeepSeek 原生 Function Calling 决定调用哪个工具；含"趋势图/折线"关键词的确定性前置规则；达到最大轮数强制熔断 |
| `app/agent/nodes/tool_exec.py` | **ReAct 执行**：执行工具并把观测结果回喂下一轮决策 |
| `app/agent/nodes/tool.py` | 工具注册表（`@tool`：查库 / 反馈统计 / 读文件 / 画图），逐个工具做数据出口权限判定 |
| `app/agent/nodes/text2sql.py` | Text-to-SQL：生成 SQL + 只读防护 + 越权校验 + 报错自愈重试 |
| `app/agent/nodes/approval.py` | HITL：`interrupt()` 挂起高危工具调用，等待 `POST /chat/approve` 恢复 |
| `app/agent/nodes/reviewer.py` | 审核器：回答必须含具体数字、不能有空话，通过则输出最终答案并写入长期记忆 |

### 📁 app/client/ —— 外部客户端

| 文件 | 作用 |
|------|------|
| `app/client/short_term.py` | **短期记忆**：优先 Redis（会话上下文，1 小时 TTL）；Redis 连不上时自动降级为进程内字典 |
| `app/client/vectorstore.py` | **向量库工厂**：FAISS（默认本地）/ Qdrant（服务化，原生 metadata 过滤）；Qdrant 不可达自动降级 FAISS |
| `app/client/__init__.py` | 包标记文件 |

### 📁 app/conf/ —— 配置层

| 文件 | 作用 |
|------|------|
| `app/conf/config.py` | **统一配置中心**。所有环境变量（API Key、模型名、Redis 地址、限流阈值等）都在这里读取，其他文件只从这里 import |

### 📁 app/model/ —— 实体层（ORM）

| 文件 | 作用 |
|------|------|
| `app/model/base.py` | SQLAlchemy `declarative_base()`，用户与业务实体共用 |
| `app/model/user.py` | `users` 表结构 + 角色常量（admin / sales / viewer） |
| `app/model/business.py` | `sales_records` / `feedback_records` 表结构 |

### 📁 app/repository/ —— 仓储层

| 文件 | 作用 |
|------|------|
| `app/repository/business.py` | 业务库引擎/会话、schema 自然语言描述、只读 SQL 校验与执行 |
| `app/repository/users.py` | 用户查询、密码哈希（PBKDF2）、登录认证与用户创建 |
| `app/repository/long_term.py` | **长期记忆**：把审核通过的问答存入 PostgreSQL/SQLite（`DATABASE_URL`），不可达时自动跳过 |

### 📁 app/service/ —— 服务层

| 文件 | 作用 |
|------|------|
| `app/service/permission.py` | **RBAC 数据权限**：`scoped_region` / `sql_within_scope` / `rag_scope_filter`，所有数据出口共用同一判定 |
| `app/service/collect.py` | 外部数据源采集：简单工厂 + 6 小时增量缓存 + 单源失败降级 |

### 📁 app/script/ —— 离线脚本层

| 文件 | 作用 |
|------|------|
| `app/script/ingest.py` | **知识库入库脚本**：读取 data/ 文档 → 切块 → bge-small-zh 向量化 → 写入 FAISS/Qdrant。更新知识库后重跑 |
| `app/script/seed_business_db.py` | 业务库种子脚本（灌销售/反馈数据 + 三个演示账号） |
| `app/script/sync_business_to_rag.py` | 业务库 → 摘要 Markdown → 重建向量索引 |
| `app/script/demo_tool_permissions.py` | 一键演示 RBAC 在各数据出口的拦截效果 |

### 📁 app/utils/ —— 通用工具

| 文件 | 作用 |
|------|------|
| `app/utils/logging_config.py` | structlog 结构化日志配置（控制台彩色 / JSON 两种格式，request_id 贯穿链路） |

### 📁 data/ —— 业务数据（RAG 的知识来源 + 统计的原始数据）

| 文件 | 作用 |
|------|------|
| `sales_q1.csv` | 2026 Q1 销售明细表（日期/产品/区域/销量/销售额），pandas 统计和环比的数据源 |
| `sales_q4_2025.csv` | 2025 Q4 销售明细表，作为"环比增长"的对比基数 |
| `customer_feedback.md` | 客户反馈原始记录（评分 + 问题类型 + 内容） |
| `feedback_stats.md` | 客户反馈统计摘要 |
| `product_info.md` | 产品介绍知识库，供知识问答检索 |
| `financial_summary.md` | 财务摘要（成本结构、净利润等） |
| `business.db` | ⚙️ **运行时自动生成的业务库 SQLite（二进制）**，不是源码，已 gitignore |
| `long_term_memory.db` | ⚙️ **运行时自动生成的长期记忆 SQLite（二进制）**，已 gitignore |
| `.collect_state.json` | ⚙️ 数据采集增量缓存状态（自动生成） |

### 📁 evals/ —— 效果评测

| 文件 | 作用 |
|------|------|
| `evals/dataset.jsonl` | 20 道带标准答案的评测题（每行一题，答案关键词锚定真实数字） |
| `evals/run_eval.py` | 评测脚本：真实跑一遍全部题目，按关键词匹配 + 可选 LLM-as-judge 判分，`--gate` 可作 CI 门禁 |
| `evals/report.json` | ⚙️ 运行评测后**自动生成的报告**，已 gitignore |

### 📁 tests/ —— 自动化测试

| 文件 | 作用 |
|------|------|
| `tests/test_smoke.py` | 逐节点 + 全链路冒烟测试，mock 掉所有外部依赖（不调真实 LLM、不需要 Redis/索引） |
| `tests/test_auth_rbac.py` | JWT 签发/篡改/过期、密码哈希、RBAC 作用域、向量库降级 |
| `tests/test_text2sql.py` | 只读 SQL 防护、SQL 提取、临时 SQLite 真执行 |

### 📁 docs/ —— 文档与图片

| 文件 | 作用 |
|------|------|
| `docs/screenshot.png` | README 里展示的 Web UI 真实运行截图 |
| `docs/OPTIMIZATION.md` | 个人复盘文档（优化记录），**仅本地保留，已 gitignore** |
| `docs/PROJECT_STRUCTURE.md` | 你正在看的这份文件（本地学习用） |

### 📁 .github/workflows/ —— CI 自动化

| 文件 | 作用 |
|------|------|
| `.github/workflows/ci.yml` | 每次 push / PR 自动执行 `ruff check` + `pytest`；配置 secret 后可跑评测门禁 |

---

## 四、一次请求的完整流转（把文件串起来）

以"分析上季度销售数据，画个趋势图"为例：

1. **浏览器** → 打开 `app/api/main.py` 提供的 `/ui` 页面，点发送
2. **`app/api/main.py`** → 限流检查 → 调用 `app/agent/service.py` 装配的工作流，通过 SSE 把每个节点的产出实时推回浏览器
3. **`nodes/router.py`** → 判定为"数据分析"类
4. **`nodes/planner.py`** → 拆成 ["统计总销售额", "产品/区域分布", "画月度趋势图"]
5. **`nodes/skill.py`** → 逐个处理：
   - 数字类：pandas 读 `data/sales_q1.csv`、`sales_q4_2025.csv` 精确计算（环比等）
   - 知识类：经 `app/script/ingest.py` 建好的向量索引检索 `data/*.md` 片段
   - 同时从 `app/client/short_term.py` 取本会话历史上下文
6. **`nodes/tool_decide.py` → `nodes/tool_exec.py`** → 命中"趋势图"关键词直接绘图；需要事实数据时经 Function Calling 调 `text2sql` 查业务库，结果回喂再决策
7. **`nodes/approval.py`** → 高危工具（业务库查询）在 `APPROVAL_MODE=true` 时挂起等待人工审批
8. **`nodes/reviewer.py`** → 审核数字是否具体、有无空话；通过则输出最终答案，并经 `app/repository/long_term.py` 存入数据库
9. **`app/api/main.py`** → 最终回答推给浏览器渲染；全程 token/耗时被 `app/agent/metrics.py` 记录，可在 `/metrics` 查看

配置贯穿全程：`app/conf/config.py` 提供所有参数，`app/agent/llm.py` 统一负责与 DeepSeek 通信。

---

## 五、文件分类速查

### 需要人写/改的源码（面试讲的就是这些）
```
main.py
app/conf/config.py
app/api/main.py  app/api/auth.py
app/agent/*.py  app/agent/nodes/*.py
app/client/*.py  app/repository/*.py  app/model/*.py  app/service/*.py
app/script/*.py
evals/run_eval.py  tests/*.py
```

### 需要人维护的数据与文本
```
data/*.csv、data/*.md    evals/dataset.jsonl
README.md  .env.example  docker-compose.yml  Dockerfile  ruff.toml
```

### ⚙️ 工具自动生成的（不要手动打开/编辑，删了也能再生成）
| 路径 | 谁生成的 | 删了怎么恢复 |
|------|----------|-------------|
| `__pycache__/`、`*.pyc` | Python 运行时 | 再运行一次自动生成 |
| `.ruff_cache/` | ruff 检查时 | 再跑一次 ruff |
| `.pytest_cache/` | pytest 运行时 | 再跑一次测试 |
| `faiss_index/`（index.faiss + index.pkl） | `python app/script/ingest.py` | 重跑入库脚本 |
| `dump.rdb` | Redis 持久化文件 | 重启 Redis |
| `data/business.db` | 业务库引擎初始化 / 种子脚本 | 重跑 `app/script/seed_business_db.py` |
| `data/long_term_memory.db` | 程序首次写长期记忆时 | 自动重建（历史数据会丢） |
| `data/.collect_state.json` | 数据采集脚本 | 自动重建 |
| `evals/report.json` | 跑评测脚本 | 重跑评测 |
| `.venv/` | 你创建虚拟环境时 | `python -m venv .venv` + 重装依赖 |
| `.idea/` | PyCharm/IDE 的个人配置 | IDE 自动重建 |

> 记忆技巧：**扩展名是 `.py/.json/.csv/.md/.yml/.toml/.txt/.bat/.example` 的基本都是文本能打开；`.pyc/.faiss/.pkl/.db/.rdb/.png` 基本都是二进制要程序读。**

---

## 六、新人上手顺序建议

1. `main.py` → 知道怎么启动
2. `app/api/main.py` → 看请求从哪进来（先只看路由定义，HTML 部分可跳过）
3. `app/agent/state.py` → 看节点之间传什么数据
4. `app/agent/service.py` → 看节点怎么连起来
5. 按 router → planner → skill → tool_decide / tool_exec → reviewer 顺序读 `app/agent/nodes/`
6. 最后看 `app/client/`、`app/repository/` 和 `app/script/ingest.py` 这些辅助模块
