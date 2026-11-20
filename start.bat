@echo off
rem ReFrameVideo launcher (Windows). First run: creates .venv and installs requirements.txt.
rem Keep this file ASCII-only so it reads the same on any Windows language.
cd /d "%~dp0"
set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" (
  where python >nul 2>nul || (echo Python not found. Install Python 3.10 or later and tick "Add python.exe to PATH". & pause & exit /b 1)
  python -c "import sys; sys.exit(sys.version_info < (3, 10))" || (echo Python 3.10 or later is required. Please upgrade Python. & pause & exit /b 1)
  echo First run: creating the virtual environment in .venv ...
  python -m venv .venv || (echo Could not create the virtual environment. See the error above. & pause & exit /b 1)
)
fc /b requirements.txt .venv\requirements.installed.txt >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies. The first time takes a few minutes ...
  "%VPY%" -m pip install --upgrade pip || goto fail
  "%VPY%" -m pip install -r requirements.txt || goto fail
  copy /y requirements.txt .venv\requirements.installed.txt >nul
)
"%VPY%" start.py %*
if errorlevel 1 pause
exit /b

:fail
echo Installing dependencies failed. See the error above. You can delete the .venv folder and run start.bat again.
pause
exit /b 1
