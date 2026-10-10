"""项目入口：启动 FastAPI 服务。"""
import uvicorn

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 多智能体协作平台启动中...")
    print("访问地址：http://localhost:8000/ui")
    print("API 文档：http://localhost:8000/docs")
    print("=" * 50)
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
