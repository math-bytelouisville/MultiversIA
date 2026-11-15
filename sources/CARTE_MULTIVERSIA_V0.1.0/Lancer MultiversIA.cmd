@echo off
setlocal
cd /d "%~dp0"
where pyw >nul 2>nul
if errorlevel 1 goto fallback
start "" pyw -3 "%~dp0app.py"
exit /b
:fallback
python "%~dp0app.py"
if errorlevel 1 pause
endlocal
