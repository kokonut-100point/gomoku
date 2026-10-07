@echo off
chcp 65001 >nul
title Gomoku Web
setlocal
call "%~dp0_find-python.bat" || goto :nopython
echo Starting the Gomoku backend...
start "gomoku-server" /min "%PYEXE%" "%~dp0gomoku_server.py" --quiet
echo Waiting for the backend to become ready...
powershell -NoProfile -Command "for($i=0;$i -lt 40;$i++){ try{ $r=Invoke-RestMethod 'http://127.0.0.1:8099/api/health' -TimeoutSec 2; if($r.ok){ Write-Host 'backend ready'; exit 0 } }catch{}; Start-Sleep -Milliseconds 300 }; Write-Host 'backend did not start - see .gomoku-server.log'; exit 1"
echo Opening browser...
start "" "http://127.0.0.1:8099"
echo.
echo Note: the backend runs in a minimized "gomoku-server" window.
echo       Close that window to stop the server.
timeout /t 3 >nul
goto :eof

:nopython
echo.
echo [ERROR] Python not found.
echo   - Install Python 3.9+ and add it to PATH, or
echo   - create a ".python-path" file in this folder containing the full
echo     path to your interpreter, or
echo   - set the GOMOKU_PY environment variable.
echo.
pause
