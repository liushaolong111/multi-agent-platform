"""FastAPI 服务 + SSE 流式接口 + Web UI。"""
import json
import logging
import os
import uuid

import structlog.contextvars
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.agent.metrics import metrics
from app.agent.service import graph
from app.api.auth import ANONYMOUS_USER, LoginRequest, get_current_user, login
from app.conf import config
from app.utils.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Multi-Agent Platform")


@app.middleware("http")
async def bind_log_context(request: Request, call_next):
    """每个请求生成 request_id 并绑定到日志上下文：

    之后该请求链路（图节点、工具、DB）打出的所有日志都自动携带
    request_id/session_id/user_id 字段，排障时可按 request_id 串起完整链路。
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=uuid.uuid4().hex[:12],
        path=request.url.path,
        session_id=request.headers.get("X-Session-Id", ""),
    )
    try:
        return await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()

# 限流：单 IP 每分钟最多 config.RATE_LIMIT_PER_MINUTE 次 /chat 请求
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS：生产环境应从环境变量配置允许的来源
allow_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_api_key(x_api_key: str = Header(default="")):
    """可选鉴权：config.API_KEY 非空时，要求请求头 X-API-Key 匹配。"""
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="无效或缺失的 API Key")


def _resolve_identity(current_user: dict, body_user_id: str) -> tuple[str, str, str]:
    """确定本次请求的身份三元组 (user_id, role, region)。

    - 带了 token（current_user 非匿名哨兵）：一律以 token 为准，客户端无法伪造身份；
    - 未带 token：沿用请求体 user_id 并赋予 admin 角色（本地开发旧行为）。
    """
    if current_user is ANONYMOUS_USER:
        return body_user_id, "admin", ""
    return current_user["user_id"], current_user["role"], current_user["region"]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = "default_user"
    session_id: str = "default_session"


class ChatResponse(BaseModel):
    answer: str
    task_type: str
    plan: list
    review_verdict: str


class ApproveRequest(BaseModel):
    session_id: str
    approved: bool


@app.get("/")
def root():
    return {"status": "ok", "service": "Multi-Agent Platform"}


@app.get("/health")
def health():
    """健康检查：报告 LLM 配置与链路追踪是否就绪。"""
    missing = config.check_llm_config()
    return {
        "status": "ok" if not missing else "degraded",
        "llm_configured": missing is None,
        "missing_config": missing,
        "langsmith_tracing": config.LANGSMITH_TRACING and bool(config.LANGSMITH_API_KEY),
    }


@app.get("/metrics")
def get_metrics():
    """运行指标快照：请求数、token 用量、LLM/节点延迟、工具调用分布。"""
    return metrics.snapshot()


@app.post("/auth/login")
def auth_login(body: LoginRequest):
    """登录：校验用户名密码，签发 JWT（AUTH_ENABLED=true 时 /chat 需要）。"""
    return login(body)


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit(f"{config.RATE_LIMIT_PER_MINUTE}/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """一次性返回接口。AUTH_ENABLED=true 时用户身份来自 JWT，不信任请求体。"""
    metrics.record_request()
    user_id, user_role, user_region = _resolve_identity(current_user, body.user_id)
    # 身份/会话信息进日志上下文，本条请求后续所有日志自动携带
    structlog.contextvars.bind_contextvars(session_id=body.session_id, user_id=user_id)
    result = await graph.ainvoke(
        {
            "user_input": body.message,
            "messages": [],
            "status": "start",
            "iteration": 0,
            "session_id": body.session_id,
            "user_id": user_id,
            "user_role": user_role,
            "user_region": user_region,
        },
        # thread_id 供 checkpointer 定位断点（interrupt 恢复用）
        config={"configurable": {"thread_id": body.session_id}},
    )

    # interrupt 挂起（APPROVAL_MODE 下等待人工审批）：返回待审批信息而非最终答案
    if "__interrupt__" in result:
        intr = result["__interrupt__"][0].value
        return ChatResponse(
            answer=(
                f"⏸️ {intr.get('message', '等待人工审批')}\n"
                f"待确认操作：{json.dumps(intr.get('calls', []), ensure_ascii=False)}\n"
                f"请调用 POST /chat/approve（session_id={body.session_id}）确认或拒绝。"
            ),
            task_type=result.get("task_type", ""),
            plan=result.get("plan", []),
            review_verdict="PENDING_APPROVAL",
        )

    return ChatResponse(
        answer=result.get("final_answer") or result.get("tool_result", ""),
        task_type=result.get("task_type", ""),
        plan=result.get("plan", []),
        review_verdict=result.get("review_verdict", ""),
    )


@app.post("/chat/approve", dependencies=[Depends(verify_api_key)])
async def chat_approve(
    body: ApproveRequest,
    current_user: dict = Depends(get_current_user),
):
    """人工审批恢复：从 interrupt 断点继续执行（APPROVAL_MODE=true 时使用）。

    仅允许"发起该会话的同一用户"审批——否则任何知道 session_id 的人
    都能放行高危操作，审批闸门形同虚设。
    """
    from langgraph.types import Command

    thread = {"configurable": {"thread_id": body.session_id}}
    # 取回挂起时的图状态，核对会话归属；取不到（无 checkpointer/无该会话）则不阻断
    try:
        snapshot = await graph.aget_state(thread)
        owner = (snapshot.values or {}).get("user_id")
    except Exception:
        owner = None
    if owner and current_user is not ANONYMOUS_USER and current_user["user_id"] != owner:
        raise HTTPException(status_code=403, detail="只能由发起该会话的用户审批")

    result = await graph.ainvoke(
        Command(resume={"approved": body.approved}),
        config=thread,
    )
    return {
        "approval": "approved" if body.approved else "rejected",
        "answer": result.get("final_answer") or result.get("tool_result", ""),
        "review_verdict": result.get("review_verdict", ""),
    }


def _node_summary(node_name: str, node_output: dict) -> tuple[str, str]:
    """把每个节点的状态输出转换成人类可读文本。

    Returns:
        (tag, text)：节点展示标签 + 正文内容。
    """
    # 防御：有些 LLM 会返回字面量 "\n"，统一替换成真实换行
    def _clean(s: str) -> str:
        return (s or "").replace("\\n", "\n")

    if node_name == "router":
        return "路由", f"任务分类：{node_output.get('task_type', 'unknown')}"

    if node_name == "planner":
        plan = node_output.get("plan") or []
        lines = [f"拆解为 {len(plan)} 个子任务："]
        for i, t in enumerate(plan, 1):
            lines.append(f"  {i}. {t}")
        return "规划", "\n".join(lines)

    if node_name == "skill":
        result = node_output.get("tool_result", "")
        task_count = result.count("【") if result else 0
        return "分析", f"已完成知识检索与分析（{task_count} 个子任务）"

    if node_name == "tool_decide":
        pending = node_output.get("pending_tool_calls") or []
        if pending:
            names = "、".join(c.get("name", "?") for c in pending)
            return "工具决策", f"决定调用：{names}"
        return "工具决策", "无需额外工具调用（ReAct 收敛）"

    if node_name == "tool_exec":
        n = node_output.get("last_tool_executed", 0)
        rounds = node_output.get("tool_rounds", 0)
        return "工具执行", f"第 {rounds} 轮：执行了 {n} 个工具调用"

    if node_name == "approval":
        status = node_output.get("approval_status", "")
        return "人工审批", "已批准放行" if status == "approved" else "已拒绝拦截"

    if node_name == "reviewer":
        verdict = node_output.get("review_verdict", "")
        answer = _clean(node_output.get("final_answer", ""))
        tag = "审核通过" if verdict == "APPROVED" else "需修改"
        return tag, answer

    return node_name, _clean(str(node_output))


@app.post("/chat/stream", dependencies=[Depends(verify_api_key)])
@limiter.limit(f"{config.RATE_LIMIT_PER_MINUTE}/minute")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """SSE 流式接口，逐节点推送执行过程。"""
    user_id, user_role, user_region = _resolve_identity(current_user, body.user_id)
    structlog.contextvars.bind_contextvars(session_id=body.session_id, user_id=user_id)

    async def event_generator():
        metrics.record_request()
        try:
            async for event in graph.astream(
                {
                    "user_input": body.message,
                    "messages": [],
                    "status": "start",
                    "iteration": 0,
                    "session_id": body.session_id,
                    "user_id": user_id,
                    "user_role": user_role,
                    "user_region": user_region,
                },
                config={"configurable": {"thread_id": body.session_id}},
                stream_mode="updates",
            ):
                # interrupt 挂起：推送待审批事件并结束流
                if "__interrupt__" in event:
                    intr = event["__interrupt__"][0].value
                    payload = {
                        "node": "approval",
                        "tag": "等待审批",
                        "approval_required": True,
                        "session_id": body.session_id,
                        "output": (
                            f"{intr.get('message', '')}\n"
                            f"待确认：{json.dumps(intr.get('calls', []), ensure_ascii=False)}"
                        ),
                        "is_final": True,
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                for node_name, node_output in event.items():
                    tag, text = _node_summary(node_name, node_output)
                    payload = {
                        "node": node_name,
                        "tag": tag,
                        "output": text,
                        "is_final": node_name == "reviewer",
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            metrics.record_request(error=True)
            logger.exception("[API] 流式请求异常")
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/ui", response_class=HTMLResponse)
async def ui():
    return """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>多智能体助手</title>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    max-width: 860px; margin: 32px auto; padding: 0 20px;
    background: #f7f8fa; color: #1f2329;
  }
  h1 { font-size: 22px; margin: 0 0 16px; }
  .input-row { display: flex; gap: 10px; margin-bottom: 20px; }
  .login-row { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
  .login-row input { flex: 0 0 150px; padding: 8px 12px; font-size: 13px; }
  .login-row button { padding: 8px 16px; font-size: 13px; }
  .login-status { font-size: 12.5px; color: #8a8f99; }
  .login-status.ok { color: #2e7d32; font-weight: 600; }
  .login-status.err { color: #c62828; }
  .quick { font-size: 12.5px; color: #4e83fd; cursor: pointer; text-decoration: underline; }
  .node-tag.approval { background: #fdecea; color: #c62828; }
  .approve-btn { background: #2e7d32; }
  .reject-btn { background: #e34d59; margin-left: 8px; }
  input {
    flex: 1; padding: 12px 14px; font-size: 14px;
    border: 1px solid #e4e6eb; border-radius: 8px; outline: none;
  }
  input:focus { border-color: #4e83fd; }
  button {
    padding: 12px 22px; font-size: 14px; cursor: pointer;
    background: #4e83fd; color: white; border: none; border-radius: 8px;
  }
  button:disabled { background: #b8c0cc; cursor: not-allowed; }
  h3 { font-size: 15px; color: #646a73; margin: 24px 0 10px; font-weight: 600; }

  /* 执行过程节点卡片 */
  .node-card {
    display: flex; gap: 12px; padding: 10px 14px; margin-bottom: 8px;
    background: white; border: 1px solid #eef0f3; border-radius: 8px;
    font-size: 13px;
  }
  .node-tag {
    flex-shrink: 0; min-width: 64px; text-align: center;
    padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;
    background: #eef2ff; color: #4e5bd6; height: fit-content;
  }
  .node-tag.router { background: #e8f5e9; color: #2e7d32; }
  .node-tag.planner { background: #fff3e0; color: #e65100; }
  .node-tag.skill { background: #e3f2fd; color: #1565c0; }
  .node-tag.tool { background: #f3e5f5; color: #7b1fa2; }
  .node-tag.reviewer { background: #e8f5e9; color: #2e7d32; }
  .node-content { flex: 1; white-space: pre-wrap; line-height: 1.6; color: #333; word-break: break-word; }

  /* 最终回答 */
  .final-answer {
    margin-top: 16px; padding: 20px 22px;
    background: white; border: 1px solid #e4e6eb; border-radius: 12px;
    line-height: 1.75; font-size: 14px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  }
  .final-answer h2, .final-answer h3, .final-answer h4 {
    margin: 14px 0 8px; color: #1f2329;
  }
  .final-answer h2 { font-size: 17px; }
  .final-answer h3 { font-size: 15px; }
  .final-answer p { margin: 6px 0; }
  .final-answer ul { margin: 6px 0; padding-left: 22px; }
  .final-answer li { margin: 3px 0; }
  .final-answer strong { color: #1f2329; }
  .final-answer table {
    border-collapse: collapse; margin: 10px 0; font-size: 13px;
  }
  .final-answer th, .final-answer td {
    border: 1px solid #e4e6eb; padding: 6px 10px; text-align: left;
  }
  .final-answer pre {
    background: #f5f6f8; padding: 10px 12px; border-radius: 6px;
    overflow-x: auto; font-size: 12.5px;
  }
  .label { font-size: 12px; color: #4e83fd; font-weight: 600; margin-bottom: 8px; }
</style>
</head>
<body>
  <h1>🤖 多智能体协作平台</h1>
  <div class="login-row">
    <input id="username" value="admin" placeholder="用户名" />
    <input id="password" type="password" value="admin123" placeholder="密码" />
    <button id="loginBtn">登录</button>
    <span id="loginStatus" class="login-status">未登录（以 admin 身份运行）</span>
    <span class="quick" data-u="admin" data-p="admin123">管理员</span>
    <span class="quick" data-u="sales_hd" data-p="sales123">华东销售</span>
    <span class="quick" data-u="viewer" data-p="viewer123">只读</span>
  </div>
  <div class="input-row">
    <input id="msg" placeholder="输入任务，例如：分析上季度销售数据" value="分析上季度销售数据" />
    <button id="send">发送</button>
  </div>

  <h3>执行过程</h3>
  <div id="output"></div>
  <div id="finalBox" style="display:none">
    <div class="label">最终回答</div>
    <div id="finalAnswer" class="final-answer"></div>
  </div>

  <script>
    const sendBtn = document.getElementById('send');
    const msgInput = document.getElementById('msg');
    const output = document.getElementById('output');
    const finalBox = document.getElementById('finalBox');
    const finalAnswer = document.getElementById('finalAnswer');
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    const loginBtn = document.getElementById('loginBtn');
    const loginStatus = document.getElementById('loginStatus');

    let token = null;      // 登录后由 /auth/login 获得，随每次请求发送
    let identity = null;   // {user_id, role, region}
    let pendingSessionId = null;
    // 每次登录换一个会话 id：避免不同身份复用同一个 checkpointer 线程造成状态串扰
    let sessionId = 'ui_' + Math.random().toString(36).slice(2, 10);

    function setStatus(text, cls) {
      loginStatus.textContent = text;
      loginStatus.className = 'login-status' + (cls ? ' ' + cls : '');
    }

    function authHeaders() {
      const h = { 'Content-Type': 'application/json' };
      if (token) h['Authorization'] = 'Bearer ' + token;
      return h;
    }

    loginBtn.onclick = async () => {
      loginBtn.disabled = true;
      try {
        const resp = await fetch('/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: usernameInput.value, password: passwordInput.value })
        });
        const data = await resp.json();
        if (!resp.ok) { setStatus('登录失败：' + (data.detail || '未知错误'), 'err'); return; }
        token = data.access_token;
        identity = data.user;
        sessionId = 'ui_' + identity.user_id + '_' + Math.random().toString(36).slice(2, 8);
        setStatus('当前身份：' + identity.user_id + '（' + identity.role +
                  (identity.region ? ' / ' + identity.region : '') + '）', 'ok');
      } catch (e) {
        setStatus('登录失败：' + e.message, 'err');
      } finally {
        loginBtn.disabled = false;
      }
    };

    // 演示账号一键填充并登录
    document.querySelectorAll('.quick').forEach(el => {
      el.onclick = () => {
        usernameInput.value = el.dataset.u;
        passwordInput.value = el.dataset.p;
        loginBtn.click();
      };
    });

    // 人工审批：批准 / 拒绝挂起中的高危工具调用
    async function sendApproval(approved) {
      if (!pendingSessionId) return;
      try {
        const resp = await fetch('/chat/approve', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ session_id: pendingSessionId, approved: approved })
        });
        const data = await resp.json();
        const box = document.getElementById('approvalBox');
        if (box) box.remove();
        pendingSessionId = null;
        if (!resp.ok) { output.textContent = '审批失败：' + (data.detail || resp.status); return; }
        finalAnswer.innerHTML =
          '<div class="label">' + (approved ? '✅ 已批准，继续执行' : '⛔ 已拒绝，已拦截该调用') + '</div>' +
          renderMarkdown(data.answer || '');
        finalBox.style.display = 'block';
      } catch (e) {
        output.textContent = '审批失败：' + e.message;
      }
    }

    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = String(str ?? '');
      return div.innerHTML;
    }

    // 轻量 Markdown 渲染：加粗 / 标题 / 列表 / 表格 / 代码块 / 折线图
    function renderMarkdown(text) {
      // 1. 先把 SVG 图表提取出来（不参与 HTML 转义）
      const charts = [];
      const safeText = text.replace(/\\[\\[CHART_BEGIN\\]\\]([\\s\\S]*?)\\[\\[CHART_END\\]\\]/g, (_, svg) => {
        const idx = charts.length;
        charts.push(svg);
        return `__CHART_${idx}__`;
      });

      let html = escapeHtml(safeText);
      // 代码块
      html = html.replace(/```([\\s\\S]*?)```/g, (_, c) => '<pre>' + c.replace(/^\\w+\\n/, '') + '</pre>');
      // 加粗
      html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
      // 标题
      html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
      html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
      html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
      // 无序列表（连续行包裹 ul）
      html = html.replace(/((?:^\\s*[-*] .+\\n?)+)/gm, m => {
        const items = m.split('\\n').filter(l => l.trim()).map(l => '<li>' + l.replace(/^\\s*[-*] /, '') + '</li>').join('');
        return '<ul>' + items + '</ul>';
      });
      // 表格（简易：| a | b | 行）
      html = html.replace(/((?:^\\|.+\\|\\s*\\n?)+)/gm, m => {
        const rows = m.trim().split('\\n').filter(r => r.includes('|'));
        if (rows.length < 2) return m;
        let table = '<table>';
        rows.forEach((r, i) => {
          const cells = r.split('|').slice(1, -1).map(c => c.trim());
          const tag = i === 0 ? 'th' : 'td';
          table += '<tr>' + cells.map(c => '<' + tag + '>' + c + '</' + tag + '>').join('') + '</tr>';
        });
        table += '</table>';
        return table;
      });
      // 普通换行
      html = html.replace(/\\n/g, '<br>');
      // 2. 把图表占位符替换回原始 SVG
      html = html.replace(/__CHART_(\\d+)__/g, (_, i) => charts[parseInt(i)] || '');
      return html;
    }

    sendBtn.onclick = async () => {
      output.innerHTML = '';
      finalBox.style.display = 'none';
      finalAnswer.innerHTML = '';
      sendBtn.disabled = true;
      sendBtn.textContent = '执行中...';

      try {
        const resp = await fetch('/chat/stream', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            message: msgInput.value,
            user_id: identity ? identity.user_id : 'u1',
            session_id: sessionId
          })
        });

        if (!resp.ok) {
          let detail = 'HTTP ' + resp.status;
          try { detail = (await resp.json()).detail || detail; } catch (e) {}
          output.textContent = '请求失败：' + detail + '（请先登录，或确认服务端已启动）';
          return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\\n\\n');
          buffer = lines.pop();

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const content = line.slice(6);
            if (content === '[DONE]') continue;

            try {
              const data = JSON.parse(content);
              if (data.approval_required) {
                // 高危工具调用挂起：渲染批准/拒绝按钮，点击后走 /chat/approve
                pendingSessionId = data.session_id || sessionId;
                const card = document.createElement('div');
                card.id = 'approvalBox';
                card.className = 'node-card';
                card.innerHTML =
                  '<div class="node-tag approval">等待审批</div>' +
                  '<div class="node-content">' + escapeHtml(data.output) +
                  '<div style="margin-top:10px">' +
                  '<button class="approve-btn" id="okBtn">批准执行</button>' +
                  '<button class="reject-btn" id="noBtn">拒绝</button>' +
                  '</div></div>';
                output.appendChild(card);
                output.scrollTop = output.scrollHeight;
                document.getElementById('okBtn').onclick = () => sendApproval(true);
                document.getElementById('noBtn').onclick = () => sendApproval(false);
              } else if (data.is_final) {
                finalAnswer.innerHTML = renderMarkdown(data.output);
                finalBox.style.display = 'block';
              } else {
                const card = document.createElement('div');
                card.className = 'node-card';
                const nodeClass = (data.node || '').toLowerCase();
                card.innerHTML =
                  '<div class="node-tag ' + nodeClass + '">' + escapeHtml(data.tag || data.node) + '</div>' +
                  '<div class="node-content">' + escapeHtml(data.output) + '</div>';
                output.appendChild(card);
                output.scrollTop = output.scrollHeight;
              }
            } catch (e) {}
          }
        }
      } catch (e) {
        output.textContent = '错误：' + e.message;
      } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = '发送';
      }
    };
  </script>
</body>
</html>
    """


if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("🚀 多智能体协作平台启动中...")
    print("访问地址：http://localhost:8000/ui")
    print("API 文档：http://localhost:8000/docs")
    print("=" * 50)
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=True)
