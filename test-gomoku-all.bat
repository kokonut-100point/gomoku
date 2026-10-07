@echo off
chcp 65001 >nul
title Gomoku Tests
setlocal
call "%~dp0_find-python.bat" || goto :nopython
echo === 1/3 core logic ===
"%PYEXE%" "%~dp0test-gomoku.py"
echo.
echo === 2/3 server API (Flask test client, no port needed) ===
"%PYEXE%" "%~dp0test-gomoku-server.py"
echo.
echo === 3/3 end-to-end over HTTP (auto-starts the backend) ===
"%PYEXE%" "%~dp0test-gomoku-client.py"
echo.
pause
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
