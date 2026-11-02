@echo off
rem One-click start for Windows. Double-click this file.
rem Needs Python 3.11+ and Node.js 18+ installed.

rem Re-open in a window that stays open, so errors stay visible.
if not "%~1"=="run" (
  cmd /k ""%~f0" run"
  exit /b
)

cd /d "%~dp0"
echo Benchmark - folder: %CD%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python was not found.
  echo     Reinstall it from https://www.python.org/downloads
  echo     and tick "Add python.exe to PATH" on the first screen.
  goto :fail
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [X] Node.js was not found. Install it from https://nodejs.org and run this again.
  goto :fail
)
python --version
call node --version

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo [1/3] Creating Python environment...
  python -m venv .venv
  if errorlevel 1 goto :fail
)
rem A half-created environment can lack pip. Repair it, or rebuild it once.
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo Repairing the Python environment...
  ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>nul
  ".venv\Scripts\python.exe" -m pip --version >nul 2>nul
  if errorlevel 1 (
    rmdir /s /q .venv
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip --version >nul 2>nul
    if errorlevel 1 (
      echo [X] Python is installed without pip.
      echo     Run the Python installer again, choose "Modify", tick "pip", and finish.
      goto :fail
    )
  )
  if exist ".venv\deps-ok" del ".venv\deps-ok"
)
if not exist ".venv\deps-ok" (
  echo.
  echo [2/3] Installing Python packages. The first time takes a few minutes...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
  if errorlevel 1 goto :fail
  echo ok> ".venv\deps-ok"
)
if not exist "frontend\node_modules\.bin\vite.cmd" (
  echo.
  echo [3/3] Installing frontend packages...
  pushd frontend
  call npm install --no-fund --no-audit
  if errorlevel 1 (
    popd
    goto :fail
  )
  popd
)

echo.
echo Starting the app. Your browser opens in a few seconds.
echo Keep this window open while you use the app. Close it to stop.
echo.
start "Benchmark API - keep open" /min /D backend cmd /k ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
timeout /t 4 /nobreak >nul
start "" http://localhost:5173
cd frontend
call npx vite --host 127.0.0.1 --port 5173
goto :eof

:fail
echo.
echo ============================================================
echo  Something went wrong. Take a screenshot of this window
echo  and send it. This window stays open.
echo ============================================================
