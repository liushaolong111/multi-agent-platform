"""FastAPI 服务 + SSE 流式接口 + Web UI。"""
import json
import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import config
from graph.metrics import metrics
from graph.service import graph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Multi-Agent Platform")

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


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = "default_user"
    session_id: str = "default_session"


class ChatResponse(BaseModel):
    answer: str
    task_type: str
    plan: list
    review_verdict: str


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


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit(f"{config.RATE_LIMIT_PER_MINUTE}/minute")
async def chat(request: Request, body: ChatRequest):
    """一次性返回接口。"""
    metrics.record_request()
    result = await graph.ainvoke(
        {
            "user_input": body.message,
            "messages": [],
            "status": "start",
            "iteration": 0,
            "session_id": body.session_id,
            "user_id": body.user_id,
        }
    )

    return ChatResponse(
        answer=result.get("final_answer") or result.get("tool_result", ""),
        task_type=result.get("task_type", ""),
        plan=result.get("plan", []),
        review_verdict=result.get("review_verdict", ""),
    )


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

    if node_name == "tool":
        merged = node_output.get("tool_result", "")
        if "【工具补充数据】" in merged:
            return "工具", "已调用工具补充数据"
        return "工具", "无需额外工具调用"

    if node_name == "reviewer":
        verdict = node_output.get("review_verdict", "")
        answer = _clean(node_output.get("final_answer", ""))
        tag = "审核通过" if verdict == "APPROVED" else "需修改"
        return tag, answer

    return node_name, _clean(str(node_output))


@app.post("/chat/stream", dependencies=[Depends(verify_api_key)])
@limiter.limit(f"{config.RATE_LIMIT_PER_MINUTE}/minute")
async def chat_stream(request: Request, body: ChatRequest):
    """SSE 流式接口，逐节点推送执行过程。"""

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
                    "user_id": body.user_id,
                },
                stream_mode="updates",
            ):
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
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: msgInput.value, user_id: 'u1', session_id: 's1' })
        });

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
              if (data.is_final) {
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
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
