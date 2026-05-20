@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   BidPrep - 配置本地虚拟环境 (.venv)
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python，请安装 Python 3.10+ 并勾选「Add to PATH」。
  if /i not "%~1"=="/q" pause
  exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys; print(sys.executable)"') do set "PYEXE=%%i"
echo 使用解释器: %PYEXE%

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 正在创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [错误] python -m venv 失败。
    if /i not "%~1"=="/q" pause
    exit /b 1
  )
) else (
  echo [1/3] 已存在 .venv，跳过创建。
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
  echo [错误] 无法激活虚拟环境。
  if /i not "%~1"=="/q" pause
  exit /b 1
)

echo [2/3] 升级 pip ...
python -m pip install -U pip

echo [3/3] 安装项目及依赖（含 openpyxl、pymupdf、fastapi 等）...
pip install -e .

if errorlevel 1 (
  echo [错误] pip install 失败。
  if /i not "%~1"=="/q" pause
  exit /b 1
)

echo.
echo 验证关键包...
python -c "import openpyxl, fastapi, uvicorn, fitz; print('OK:', openpyxl.__name__, fastapi.__name__)"

echo.
echo 完成。请运行 start.bat 启动服务。
if /i not "%~1"=="/q" pause
