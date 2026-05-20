@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 与 api/main.py 双保险：将 src 加入模块搜索路径
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.10+ 并加入 PATH。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [提示] 未检测到 .venv，将先执行环境配置...
  call "%~dp0setup_venv.bat" /q
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
  echo [错误] 无法激活 .venv，请运行 setup_venv.bat。
  pause
  exit /b 1
)

REM 虚拟环境里缺包时自动补装（例如曾手动删过 site-packages）
python -c "import openpyxl, fastapi, uvicorn, fitz" 2>nul
if errorlevel 1 (
  echo [提示] 依赖不完整，正在 pip install -e . ...
  python -m pip install -U pip -q
  pip install -e .
  if errorlevel 1 (
    echo [错误] 安装失败，请手动运行 setup_venv.bat
    pause
    exit /b 1
  )
)

echo.
echo ========================================
echo   BidPrep - 启动 API  http://127.0.0.1:8000
echo   虚拟环境: .venv  按 Ctrl+C 停止服务
echo ========================================
echo.

REM 约 3 秒后自动打开浏览器
start "" cmd /min /c "ping -n 4 127.0.0.1 >nul && start http://127.0.0.1:8000/"

python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

pause
