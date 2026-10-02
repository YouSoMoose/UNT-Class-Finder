@echo off
setlocal
cd /d "%~dp0"
if not exist .scraper-venv\Scripts\python.exe (
  py -3 -m venv .scraper-venv
  if errorlevel 1 (
    echo Install Python 3.10 or newer with the Windows py launcher.
    pause
    exit /b 1
  )
)
if not exist .scraper-venv\.ready (
  .scraper-venv\Scripts\python.exe -m pip install -r scraper\requirements.txt
  if errorlevel 1 goto failed
  .scraper-venv\Scripts\python.exe -m playwright install chromium
  if errorlevel 1 goto failed
  echo Ready>.scraper-venv\.ready
)
.scraper-venv\Scripts\python.exe scrape_fall.py --interactive
pause
exit /b
:failed
echo Scraper setup failed. Check your connection and retry setup.
pause
exit /b 1
