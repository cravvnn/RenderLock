@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
title RenderLock - startup log
pushd "%~dp0"
if not "%ERRORLEVEL%"=="0" goto folder_failed
if not exist "logs" mkdir "logs" >nul 2>&1
set "RENDERLOCK_STARTUP_LOG=%CD%\logs\RenderLock_startup.log"
>"%RENDERLOCK_STARTUP_LOG%" echo RenderLock startup
if not "%ERRORLEVEL%"=="0" goto temp_log
goto start

:temp_log
set "RENDERLOCK_STARTUP_LOG=%TEMP%\RenderLock_startup.log"
>"%RENDERLOCK_STARTUP_LOG%" echo RenderLock startup
if not "%ERRORLEVEL%"=="0" goto log_failed

:start
echo RenderLock - Replay to Blender
echo.
echo Setting up Python dependencies and opening RenderLock.
echo First setup may take a few minutes. Progress is saved to:
echo "%RENDERLOCK_STARTUP_LOG%"
echo.
echo Keep this window open while using RenderLock.
echo You can open the log in Notepad to check setup progress.
echo.
>>"%RENDERLOCK_STARTUP_LOG%" echo Started: %DATE% %TIME%
>>"%RENDERLOCK_STARTUP_LOG%" echo Program folder: "%CD%"
if not exist "Launch_RenderLock_Windows.cmd" goto incomplete
rem Source checkouts use their existing environment; release ZIPs keep verification.
if exist ".git" if exist "Launch_RenderLock_Development.bat" goto development
cmd.exe /d /c .\Launch_RenderLock_Windows.cmd >>"%RENDERLOCK_STARTUP_LOG%" 2>&1
goto finished

:development
echo Opening the development checkout.
cmd.exe /d /c .\Launch_RenderLock_Development.bat >>"%RENDERLOCK_STARTUP_LOG%" 2>&1

:finished
set "RENDERLOCK_EXIT_CODE=%ERRORLEVEL%"
>>"%RENDERLOCK_STARTUP_LOG%" echo Launcher exit code: %RENDERLOCK_EXIT_CODE%
echo.
type "%RENDERLOCK_STARTUP_LOG%"
echo.
echo RenderLock exit code: %RENDERLOCK_EXIT_CODE%
echo If the RenderLock window did not open, send this file:
echo "%RENDERLOCK_STARTUP_LOG%"
echo.
pause
popd
exit /b %RENDERLOCK_EXIT_CODE%

:incomplete
>>"%RENDERLOCK_STARTUP_LOG%" echo ERROR: Launch_RenderLock_Windows.cmd is missing. Extract the complete program ZIP.
type "%RENDERLOCK_STARTUP_LOG%"
echo Extract the complete ZIP before launching. Do not run inside the ZIP preview.
pause
popd
exit /b 1

:folder_failed
echo Could not open the program folder. Extract the complete ZIP to a writable folder.
pause
exit /b 1

:log_failed
echo Could not create a startup log in the program folder or Windows TEMP folder.
echo Extract the complete ZIP to a writable folder and try again.
pause
popd
exit /b 1
