@echo off
rem Knowledge Base Star Map - Setup and Build (Windows)
rem ASCII-only on purpose: cmd.exe may mis-handle UTF-8 .bat files.
rem Chinese step-by-step guide: open the .md file in this folder.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo [ERROR] Python 3.9+ was not found.
  echo.
  echo Install it from https://www.python.org/downloads/
  echo IMPORTANT: check 'Add Python to PATH' during installation.
  echo.
  pause
  exit /b 1
)
echo Using Python:
%PY% --version
echo.
%PY% installer.py
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" echo [ERROR] Setup did not finish. See messages above.
pause
exit /b %EXITCODE%
