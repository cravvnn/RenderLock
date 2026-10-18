@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" goto missing_python
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
echo RenderLock - development checkout
echo Running local source with the existing project environment.
".venv\Scripts\python.exe" -u -X faulthandler -m agr gui
set "RENDERLOCK_DEV_EXIT=%ERRORLEVEL%"
if not "%RENDERLOCK_DEV_EXIT%"=="0" pause
popd
exit /b %RENDERLOCK_DEV_EXIT%
:missing_python
echo This development checkout requires .venv\Scripts\python.exe.
echo Use Launch_RenderLock.bat in the extracted release for a packaged installation.
pause
popd
exit /b 1
