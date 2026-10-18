@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
rem This worker is called by Launch_RenderLock.bat, which logs output and always pauses.
rem Compare with ZERO explicitly: Python Install Manager can return negative errors.
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo RenderLock Windows launcher 0.1.6
echo Windows version:
ver
echo Looking for 64-bit Python 3.12 or newer.
if exist ".venv\Scripts\python.exe" goto try_venv
goto try_override

:try_venv
echo Checking existing local environment...
".venv\Scripts\python.exe" -c "import struct,sys; print(sys.executable,sys.version); sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)"
if not "%ERRORLEVEL%"=="0" goto try_override
".venv\Scripts\python.exe" -u bootstrap.py
exit /b %ERRORLEVEL%

:try_override
if not defined RENDERLOCK_PYTHON_EXE goto try_py312
set "RENDERLOCK_CANDIDATE=%RENDERLOCK_PYTHON_EXE%"
call :probe_direct
if not "%ERRORLEVEL%"=="0" goto try_py312
goto launch_direct

:try_py312
echo Checking Python launcher -3.12...
py -3.12 -c "import struct,sys; print(sys.executable,sys.version); sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)"
if not "%ERRORLEVEL%"=="0" goto try_py3
py -3.12 -u bootstrap.py
exit /b %ERRORLEVEL%

:try_py3
echo Checking Python launcher -3...
py -3 -c "import struct,sys; print(sys.executable,sys.version); sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)"
if not "%ERRORLEVEL%"=="0" goto try_python
py -3 -u bootstrap.py
exit /b %ERRORLEVEL%

:try_python
echo Checking python on PATH...
python -c "import struct,sys; print(sys.executable,sys.version); sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)"
if not "%ERRORLEVEL%"=="0" goto try_local312
python -u bootstrap.py
exit /b %ERRORLEVEL%

:try_local312
set "RENDERLOCK_CANDIDATE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :probe_direct
if not "%ERRORLEVEL%"=="0" goto try_system312
goto launch_direct

:try_system312
set "RENDERLOCK_CANDIDATE=%ProgramFiles%\Python312\python.exe"
call :probe_direct
if not "%ERRORLEVEL%"=="0" goto try_manager312
goto launch_direct

:try_manager312
set "RENDERLOCK_CANDIDATE=%LOCALAPPDATA%\Python\pythoncore-3.12-64\python.exe"
call :probe_direct
if not "%ERRORLEVEL%"=="0" goto missing_python
goto launch_direct

:launch_direct
"%RENDERLOCK_CANDIDATE%" -u bootstrap.py
exit /b %ERRORLEVEL%

:probe_direct
echo Checking direct Python path: "%RENDERLOCK_CANDIDATE%"
if not exist "%RENDERLOCK_CANDIDATE%" exit /b 1
"%RENDERLOCK_CANDIDATE%" -c "import struct,sys; print(sys.executable,sys.version); sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)"
exit /b %ERRORLEVEL%

:missing_python
echo ERROR: A working 64-bit Python 3.12 or newer was not found.
echo Python 3.12.14 satisfies the version requirement; the patch number is not the issue.
echo RenderLock can be on a different drive from Python. Moving RenderLock to C: will not fix discovery.
echo Installed runtime list and command locations follow. No runtime is installed automatically.
py -0p
where.exe py
where.exe python
echo If Python is installed in a custom folder, set RENDERLOCK_PYTHON_EXE to its python.exe path.
echo If only Python Install Manager is installed, install a Python runtime with pip and Tcl/Tk.
echo See README.md for the manual py install 3.12 command, then launch RenderLock again.
echo Blender is installed separately. Its bundled Python is not used for this window.
exit /b 1
