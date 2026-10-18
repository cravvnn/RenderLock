@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "RENDERLOCK_LOG_DIR=%LOCALAPPDATA%\RenderLock\logs"
if not defined LOCALAPPDATA set "RENDERLOCK_LOG_DIR=%TEMP%\RenderLock\logs"
if not exist "%RENDERLOCK_LOG_DIR%" mkdir "%RENDERLOCK_LOG_DIR%"
if not exist "%~dp0runtime\python.exe" goto incomplete
"%~dp0runtime\python.exe" -I -B -u "%~dp0portable_launcher.py" %* >"%RENDERLOCK_LOG_DIR%\startup.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" exit /b 0
type "%RENDERLOCK_LOG_DIR%\startup.log"
echo.
echo Startup log: "%RENDERLOCK_LOG_DIR%\startup.log"
pause
exit /b %EXIT_CODE%
:incomplete
echo The portable runtime is missing. Extract the complete RenderLock ZIP into a new folder.
pause
exit /b 1
