"""FastAPI 服务 + SSE 流式接口 + Web UI。"""
import html
import json
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
from graph.service import graph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Multi-Agent Platform")

# CORS：生产环境应从环境变量配置允许的来源
allow_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    """健康检查：报告 LLM 配置是否就绪。"""
    missing = config.check_llm_config()
    return {
        "status": "ok" if not missing else "degraded",
        "llm_configured": missing is None,
        "missing_config": missing,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """一次性返回接口。"""
    result = await graph.ainvoke(
        {
            "user_input": request.message,
            "messages": [],
            "status": "start",
            "iteration": 0,
            "session_id": request.session_id,
            "user_id": request.user_id,
        }
    )

    return ChatResponse(
        answer=result.get("final_answer") or result.get("tool_result", ""),
        task_type=result.get("task_type", ""),
        plan=result.get("plan", []),
        review_verdict=result.get("review_verdict", ""),
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式接口，逐节点推送执行过程。"""

    async def event_generator():
        try:
            async for event in graph.astream(
                {
                    "user_input": request.message,
                    "messages": [],
                    "status": "start",
                    "iteration": 0,
                    "session_id": request.session_id,
                    "user_id": request.user_id,
                },
                stream_mode="updates",
            ):
                for node_name, node_output in event.items():
                    payload = {
                        "node": node_name,
                        "output": str(node_output),
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
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
  body { font-family: system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }
  h1 { color: #333; }
  #output { background: #f5f5f5; border-radius: 8px; padding: 16px; min-height: 200px; white-space: pre-wrap; font-family: monospace; font-size: 13px; }
  #output .node { color: #0066cc; font-weight: bold; }
  #output .content { color: #333; }
  input { width: 70%; padding: 10px; font-size: 14px; }
  button { padding: 10px 20px; font-size: 14px; cursor: pointer; background: #0066cc; color: white; border: none; border-radius: 4px; }
  button:disabled { background: #999; }
</style>
</head>
<body>
  <h1>🤖 多智能体协作平台</h1>
  <input id="msg" placeholder="输入任务，例如：分析上季度销售数据" value="分析上季度销售数据" />
  <button id="send">发送</button>
  <h3>执行过程：</h3>
  <div id="output"></div>

  <script>
    const sendBtn = document.getElementById('send');
    const msgInput = document.getElementById('msg');
    const output = document.getElementById('output');

    // 转义 HTML，防止 XSS
    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = String(str);
      return div.innerHTML;
    }

    sendBtn.onclick = async () => {
      output.innerHTML = '';
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
              const div = document.createElement('div');
              // 使用转义后的内容，避免 XSS
              div.innerHTML = '<span class="node">[' + escapeHtml(data.node) + ']</span> '
                            + '<span class="content">' + escapeHtml(data.output) + '</span>';
              output.appendChild(div);
              output.scrollTop = output.scrollHeight;
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
