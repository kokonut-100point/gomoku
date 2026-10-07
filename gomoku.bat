@echo off
chcp 65001 >nul
title Gomoku - GUI
setlocal
call "%~dp0_find-python.bat" || goto :nopython
"%PYEXE%" "%~dp0gomoku.py" %*
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
