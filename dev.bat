@echo off
rem 一键入口，不需要 activate —— 直接用 .venv 里的 python。
rem   dev            聊天页 (streamlit)   http://localhost:8501
rem   dev api        HTTP API (fastapi)   http://127.0.0.1:8000/docs
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
if /i "%CMD%"=="ui"   ( "%PY%" -m streamlit run "%~dp0partnerdesk\ui.py" & exit /b )
if /i "%CMD%"=="api"  ( "%PY%" -m uvicorn partnerdesk.app:app --port 8000 & exit /b )
if /i "%CMD%"=="test" ( "%PY%" -m pytest -q %2 %3 %4 & exit /b )
if /i "%CMD%"=="lint" ( "%PY%" -m ruff check partnerdesk tests evals & exit /b )
if /i "%CMD%"=="eval" ( "%PY%" "%~dp0evals\run_po_extract.py" %2 %3 %4 & exit /b )
if /i "%CMD%"=="py"   ( "%PY%" %2 %3 %4 %5 %6 %7 %8 %9 & exit /b )
echo Unknown command: %CMD%   (use: ui api test lint eval py)
exit /b 1