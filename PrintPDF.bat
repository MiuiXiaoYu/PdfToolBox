@echo off
setlocal
rem Launcher for PrintPDF.ps1
rem Double-click: asks for a folder and prints every PDF inside it.
rem With arguments: passes them through, e.g.
rem   PrintPDF.bat -Folder "D:\invoices" -Printer "HP LaserJet M404"

set "SCRIPT=%~dp0PrintPDF.ps1"
if not exist "%SCRIPT%" (
  echo [x] PrintPDF.ps1 not found next to this file.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"

if "%~1"=="" pause
exit /b %RC%
