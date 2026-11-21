@echo off
setlocal
cd /d "%~dp0"

rem ---- SumatraPDF path (no hardcoded machine path) ---------------------
rem Priority: 1) config.ini  (sumatra_path=..., copy config.example first)
rem            2) SumatraPath.txt next to this script (one line, no quotes)
rem            3) a SumatraPDF*.exe next to this script
rem If none exists, set SUMATRA yourself below.
set "SUMATRA="

if exist "%~dp0config.ini" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0config.ini") do (
    if /i "%%A"=="sumatra_path" if not defined SUMATRA set "SUMATRA=%%B"
  )
)
if not defined SUMATRA (
  if exist "%~dp0SumatraPath.txt" (
    for /f "usebackq delims=" %%P in ("%~dp0SumatraPath.txt") do if not defined SUMATRA set "SUMATRA=%%P"
  )
)
if not defined SUMATRA (
  for %%E in ("%~dp0SumatraPDF*.exe") do if not defined SUMATRA set "SUMATRA=%%~fE"
)

if not defined SUMATRA (
  echo [x] SumatraPDF not found.
  echo     Put the full path into SumatraPath.txt next to this script,
  echo     or drop a SumatraPDF*.exe here, or edit SUMATRA below.
  pause
  exit /b 1
)

if not exist "%SUMATRA%" (
  echo [x] SumatraPDF not found: %SUMATRA%
  echo     fix SumatraPath.txt or the SUMATRA variable, then rerun.
  pause
  exit /b 1
)

echo [i] building single-file exe ...
python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name PdfToolbox ^
  --collect-all tkinterdnd2 ^
  --add-binary "%SUMATRA%;." ^
  pdf_toolbox.py

if errorlevel 1 (
  echo.
  echo [x] build failed
  pause
  exit /b 1
)

echo.
echo [+] done: dist\PdfToolbox.exe
pause
