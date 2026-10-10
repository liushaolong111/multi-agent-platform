@echo off
echo ============================================
echo   多智能体平台 - 一键启动
echo ============================================

echo [1/2] 启动 Redis...
start "Redis" /min C:\Redis\redis-server.exe C:\Redis\redis.windows.conf
timeout /t 2 /nobreak >nul

echo [2/2] 启动 FastAPI 服务...
echo.
echo   访问地址:
echo   - Web UI:  http://localhost:8000/ui
echo   - API 文档: http://localhost:8000/docs
echo   - 健康检查: http://localhost:8000/health
echo.
echo   按 Ctrl+C 停止服务
echo ============================================
.venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
