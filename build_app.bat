@echo off
REM Build a standalone Max2OSC.exe on Windows.
REM   py -m venv .venv
REM   build_app.bat
REM Windows support is untested on real hardware -- see README.md.
setlocal
cd /d "%~dp0"
if "%PY%"=="" set PY=.venv\Scripts\python.exe
"%PY%" -m pip install --quiet -r requirements.txt -r requirements-dev.txt || goto :fail
"%PY%" tools\check_offline.py || goto :fail
"%PY%" -m PyInstaller --noconfirm --clean Max2OSC.spec || goto :fail
echo.
echo built: dist\Max2OSC\Max2OSC.exe
echo Keep Max2OSC.exe together with the _internal folder beside it.
endlocal
exit /b 0

:fail
echo.
echo Build failed.
endlocal
exit /b 1
