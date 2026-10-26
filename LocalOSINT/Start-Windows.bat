@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 app.py
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (python app.py) else (
    echo Python 3 was not found. This source starter does not include a bundled Windows runtime.
    echo Download a packaged release when available, or install Python 3.10+ and retry.
    pause
    exit /b 1
  )
)
if errorlevel 1 (
  echo The application exited with an error. See the console output above.
  pause
)
endlocal
