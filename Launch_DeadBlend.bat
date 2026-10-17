@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
title DeadBlend - startup log
pushd "%~dp0"
if not "%ERRORLEVEL%"=="0" goto folder_failed
if not exist "logs" mkdir "logs" >nul 2>&1
set "DEADBLEND_STARTUP_LOG=%CD%\logs\DeadBlend_startup.log"
>"%DEADBLEND_STARTUP_LOG%" echo DeadBlend startup
if not "%ERRORLEVEL%"=="0" goto temp_log
goto start

:temp_log
set "DEADBLEND_STARTUP_LOG=%TEMP%\DeadBlend_startup.log"
>"%DEADBLEND_STARTUP_LOG%" echo DeadBlend startup
if not "%ERRORLEVEL%"=="0" goto log_failed

:start
echo DeadBlend - Replay to Blender
echo.
echo Setting up Python dependencies and opening DeadBlend.
echo First setup may take a few minutes. Progress is saved to:
echo "%DEADBLEND_STARTUP_LOG%"
echo.
echo Keep this window open while using DeadBlend.
echo You can open the log in Notepad to check setup progress.
echo.
>>"%DEADBLEND_STARTUP_LOG%" echo Started: %DATE% %TIME%
>>"%DEADBLEND_STARTUP_LOG%" echo Program folder: "%CD%"
if not exist "Launch_DeadBlend_Windows.cmd" goto incomplete
rem Source checkouts use their existing environment; release ZIPs keep verification.
if exist ".git" if exist "Launch_DeadBlend_Development.bat" goto development
cmd.exe /d /c .\Launch_DeadBlend_Windows.cmd >>"%DEADBLEND_STARTUP_LOG%" 2>&1
goto finished

:development
echo Opening the development checkout.
cmd.exe /d /c .\Launch_DeadBlend_Development.bat >>"%DEADBLEND_STARTUP_LOG%" 2>&1

:finished
set "DEADBLEND_EXIT_CODE=%ERRORLEVEL%"
>>"%DEADBLEND_STARTUP_LOG%" echo Launcher exit code: %DEADBLEND_EXIT_CODE%
echo.
type "%DEADBLEND_STARTUP_LOG%"
echo.
echo DeadBlend exit code: %DEADBLEND_EXIT_CODE%
echo If the DeadBlend window did not open, send this file:
echo "%DEADBLEND_STARTUP_LOG%"
echo.
pause
popd
exit /b %DEADBLEND_EXIT_CODE%

:incomplete
>>"%DEADBLEND_STARTUP_LOG%" echo ERROR: Launch_DeadBlend_Windows.cmd is missing. Extract the complete program ZIP.
type "%DEADBLEND_STARTUP_LOG%"
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
