@echo off
:: _find-python.bat - resolve the Python interpreter into PYEXE for the other bats.
:: Kept ASCII-only on purpose: cmd mis-parses UTF-8 CJK text in batch files.
::
:: Lookup order:
::   1. GOMOKU_PY environment variable (temporary override)
::   2. .python-path file next to this script (one line, full interpreter path)
::   3. "python" then "py" from PATH
:: Exits with code 1 when nothing usable is found; callers print the hint.
setlocal
set "PYEXE="

if defined GOMOKU_PY set "PYEXE=%GOMOKU_PY%"

if not defined PYEXE if exist "%~dp0.python-path" (
  for /f "usebackq delims=" %%i in ("%~dp0.python-path") do if not defined PYEXE set "PYEXE=%%i"
)

if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py"

if not defined PYEXE endlocal & exit /b 1

"%PYEXE%" -c "import sys" >nul 2>nul
if errorlevel 1 endlocal & exit /b 1

endlocal & set "PYEXE=%PYEXE%" & exit /b 0
