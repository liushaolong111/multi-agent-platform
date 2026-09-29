import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
key = os.getenv("DEEPSEEK_API_KEY")

if not key:
    print("❌ 未找到 DEEPSEEK_API_KEY，请检查 .env 文件")
    exit(1)

print(f"✅ API Key 已加载，前 8 位：{key[:8]}...")

client = OpenAI(
    api_key=key,
    base_url="https://api.deepseek.com"
)

try:
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": "请回复'连接成功'四个字"}]
    )
    print(f"✅ DeepSeek 响应：{response.choices[0].message.content}")
except Exception as e:
    print(f"❌ API 调用失败：{e}")