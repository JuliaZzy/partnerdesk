@echo off
rem 一键入口，不需要 activate —— 直接用 .venv 里的 python。
rem   dev            桌面 (Vite)：聊天 / 产品 / 订单 / 合同 / 报表 / 品牌知识 / 记忆   http://localhost:5173
rem   dev api        HTTP API (fastapi)   http://127.0.0.1:8000/docs
rem   dev kill       结束占用 5173 / 8000 的进程
rem   dev db         建库（读 PARTNERDESK_DB_URL），首次运行需要
rem   dev catalog    把 data/off/ 的 Open Food Facts 快照导入产品目录（先跑 dev py scripts\fetch_off_products.py）
rem   dev test       pytest
rem   dev lint       ruff
rem   dev eval       evals/run_po_extract.py（需要真实模型）
rem   dev py ...     用 venv 的 python 跑任意命令
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo .venv not found. Run:  python -m venv .venv  then  .venv\Scripts\python -m pip install -e ".[contract,dev]"
  exit /b 1
)
set "CMD=%~1"
if "%CMD%"=="" set "CMD=ui"
if /i "%CMD%"=="ui"   goto :ui
if /i "%CMD%"=="web"  goto :ui
if /i "%CMD%"=="api"  ( "%PY%" -m uvicorn partnerdesk.app:app --host 127.0.0.1 --port 8000 & exit /b )
if /i "%CMD%"=="kill" goto :killports
if /i "%CMD%"=="stop" goto :killports
if /i "%CMD%"=="db"   ( "%PY%" "%~dp0scripts\create_database.py" & exit /b )
if /i "%CMD%"=="catalog" ( "%PY%" "%~dp0scripts\build_catalog.py" %2 %3 & exit /b )
if /i "%CMD%"=="test" ( "%PY%" -m pytest -q %2 %3 %4 & exit /b )
if /i "%CMD%"=="lint" ( "%PY%" -m ruff check partnerdesk tests evals scripts & exit /b )
if /i "%CMD%"=="eval" ( "%PY%" "%~dp0evals\run_po_extract.py" %2 %3 %4 & exit /b )
if /i "%CMD%"=="py"   ( "%PY%" %2 %3 %4 %5 %6 %7 %8 %9 & exit /b )
echo Unknown command: %CMD%   (use: ui api kill db catalog test lint eval py)
exit /b 1

:ui
where npm >nul 2>&1
if errorlevel 1 (
  echo Node.js is required for the desk. Install it, then run dev again.
  exit /b 1
)
netstat -ano | findstr /C:":8000 " | findstr LISTENING >nul
if errorlevel 1 (
  echo API -^> http://127.0.0.1:8000/docs
  start "partnerdesk-api" /D "%~dp0" "%PY%" -m uvicorn partnerdesk.app:app --host 127.0.0.1 --port 8000
)
echo Desk -^> http://localhost:5173
cd /d "%~dp0web"
if not exist node_modules call npm install
call npm run dev
exit /b

:killports
call :killport 5173
call :killport 8000
exit /b 0

:killport
set "KILLED="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":%~1 " ^| findstr LISTENING') do (
  echo Killing PID %%p on port %~1
  taskkill /PID %%p /F >nul 2>&1
  set "KILLED=1"
)
if not defined KILLED echo Nothing listening on %~1
exit /b 0
