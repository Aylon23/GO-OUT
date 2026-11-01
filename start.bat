@echo off
rem One-click start for Windows. Double-click this file.
rem Needs Python 3.11+ and Node.js 18+ installed.
cd /d "%~dp0"

where python >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads and tick "Add python.exe to PATH". & pause & exit /b 1)
where npm >nul 2>nul || (echo Node.js is not installed. Get it from https://nodejs.org & pause & exit /b 1)

if not exist .venv\Scripts\python.exe (
  echo Creating Python environment...
  python -m venv .venv || (pause & exit /b 1)
)
if not exist .venv\deps-ok (
  echo Installing Python packages, this takes a few minutes the first time...
  .venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt || (pause & exit /b 1)
  echo ok> .venv\deps-ok
)
if not exist frontend\node_modules (
  echo Installing frontend packages...
  pushd frontend
  call npm install --no-fund --no-audit || (popd & pause & exit /b 1)
  popd
)

echo Starting the app. Keep this window open. Close it to stop.
start "Benchmark API" /min cmd /c "cd backend && ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
timeout /t 3 /nobreak >nul
start "" http://localhost:5173
cd frontend
call npx vite --host 127.0.0.1 --port 5173
