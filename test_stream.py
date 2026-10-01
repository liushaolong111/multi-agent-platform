import json
import httpx

url = "http://localhost:8000/chat/stream"
payload = {"message": "分析销售数据", "user_id": "u1", "session_id": "s1"}

print("=== 开始接收流式响应 ===\n")

with httpx.stream("POST", url, json=payload, timeout=60) as response:
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data: "):
            content = line[6:]  # 去掉 "data: " 前缀
            if content == "[DONE]":
                print("\n=== 流式结束 ===")
                break
            try:
                data = json.loads(content)
                node = data.get("node", "?")
                output = data.get("output", "")[:150]
                print(f"[{node}] {output}")
                print()
            except json.JSONDecodeError:
                print(f"原始数据: {content}")