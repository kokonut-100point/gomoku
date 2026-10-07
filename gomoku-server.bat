@echo off
chcp 65001 >nul
title Gomoku Server
setlocal
call "%~dp0_find-python.bat" || goto :nopython
echo ============================================
echo   Gomoku backend server
echo   Web board : http://127.0.0.1:8099
echo   Stop      : Ctrl+C or close this window
echo ============================================
echo.
"%PYEXE%" "%~dp0gomoku_server.py" %*
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
