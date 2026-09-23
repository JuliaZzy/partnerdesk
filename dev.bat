@echo off
rem 一键入口，不需要 activate —— 直接用 .venv 里的 python。
rem   dev            桌面 (streamlit)：聊天 / 订单 / 合同 / 报表 / 品牌知识 / 记忆   http://localhost:8501
rem   dev api        HTTP API (fastapi)   http://127.0.0.1:8000/docs
rem   dev kill       查并结束占用 8501 的进程
rem   dev db         建库（读 PARTNERDESK_DB_URL；sqlite 下是 no-op）
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
if /i "%CMD%"=="ui"   (
  echo Desk -^> http://localhost:8501
  set PYTHONUNBUFFERED=1
  "%PY%" -u -m streamlit run "%~dp0partnerdesk\ui.py" --server.headless true --server.port 8501
  exit /b
)
if /i "%CMD%"=="api"  ( "%PY%" -m uvicorn partnerdesk.app:app --port 8000 & exit /b )
if /i "%CMD%"=="kill" goto :kill8501
if /i "%CMD%"=="stop" goto :kill8501
if /i "%CMD%"=="db"   ( "%PY%" "%~dp0scripts\create_database.py" & exit /b )
if /i "%CMD%"=="test" ( "%PY%" -m pytest -q %2 %3 %4 & exit /b )
if /i "%CMD%"=="lint" ( "%PY%" -m ruff check partnerdesk tests evals & exit /b )
if /i "%CMD%"=="eval" ( "%PY%" "%~dp0evals\run_po_extract.py" %2 %3 %4 & exit /b )
if /i "%CMD%"=="py"   ( "%PY%" %2 %3 %4 %5 %6 %7 %8 %9 & exit /b )
echo Unknown command: %CMD%   (use: ui api kill test lint eval py)
exit /b 1

:kill8501
set "KILLED="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":8501 " ^| findstr LISTENING') do (
  echo Killing PID %%p on port 8501
  taskkill /PID %%p /F
  set "KILLED=1"
)
if not defined KILLED echo Nothing listening on 8501
exit /b 0