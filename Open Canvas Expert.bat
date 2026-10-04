@echo off
setlocal
cd /d "%~dp0api"

REM --- Find a tested Windows x64 Python or install 3.13 for this user ------------
REM OCR native wheels and bundled models have been verified on Python 3.13 and
REM 3.14 x64. A newer or ARM64 interpreter needs its own dependency proof before
REM this launcher accepts it.
REM Every probe below is read with `||`, never with `if errorlevel 1`. The Python
REM Install Manager (the `py` that ships with 3.14+) exits with a large NEGATIVE
REM code when no runtime matches, and `if errorlevel 1` only matches codes >= 1, so
REM a missing runtime read here as success and setup then died further down at
REM `-m venv` with "No runtime installed that matches 3.13".
set "PY_CMD="
set "PY_ARGS="
call :use_python_if_supported py -3
if not defined PY_CMD call :use_python_if_supported python
if not defined PY_CMD call :use_python_if_supported py -3.14
if not defined PY_CMD call :use_python_if_supported py -3.13
if not defined PY_CMD (
  echo.
  echo Python 3.13 or 3.14 on Windows x64 is needed for setup.
  echo Installing it for your Windows user account now. No admin rights are needed.
  echo.
  where winget >nul 2>&1 || goto :python_missing
  winget install --id Python.Python.3.13 --source winget --scope user --silent --accept-source-agreements --accept-package-agreements || goto :python_install_failed
  set "PATH=%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%PATH%"
  call :use_python_if_supported "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  if not defined PY_CMD call :use_python_if_supported py -3
  if not defined PY_CMD call :use_python_if_supported python
)
if not defined PY_CMD goto :python_install_failed

REM --- Where this app's dependencies live ----------------------------------------
REM A private environment, not the shared per-user site-packages that
REM `pip install --user` writes to. That folder is shared with every other Python
REM tool on the machine, so installing there meant this app could break unrelated
REM software and unrelated software could break this app, with no signal either
REM way. Environment and marker both sit in %LOCALAPPDATA% (per-user, no admin)
REM and outside the app folder, so a self-update's whole-folder mirror leaves
REM them alone.
set "CE_DATA=%LOCALAPPDATA%\CanvasExpert"
set "VENV_DIR=%CE_DATA%\venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "MARKER=%CE_DATA%\reqs.hash"

REM An environment whose base Python was upgraded or uninstalled still has a
REM python.exe sitting on disk but can no longer run it, so prove the thing
REM works rather than trusting the file to be there.
set "VENV_OK="
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import platform, sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 13), (3, 14)) and platform.machine().lower() in ('amd64', 'x86_64') else 1)" >nul 2>&1
  if not errorlevel 1 set "VENV_OK=1"
)

if not defined VENV_OK (
  if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
  echo.
  echo First-time setup - building a private Python environment for this app.
  echo It goes in your own user folder and needs no admin rights.
  echo.
  if not exist "%CE_DATA%" mkdir "%CE_DATA%"
  %PY_CMD% %PY_ARGS% -m venv "%VENV_DIR%" || goto :setup_failed
  REM A new environment holds none of the dependencies, whatever the marker says.
  if exist "%MARKER%" del "%MARKER%"
)

REM --- First-run / dependency-change guard --------------------------------------
REM Provision only when requirements.txt differs from what we last installed, so
REM normal launches go straight to the app. The marker sits beside the environment
REM it describes, so removing either one re-provisions both.
set "CUR="
for /f "skip=1 delims=" %%H in ('certutil -hashfile requirements.txt SHA256 2^>nul') do (
  if not defined CUR set "CUR=%%H"
)
set "OLD="
if exist "%MARKER%" set /p OLD=<"%MARKER%"

if not "%CUR%"=="%OLD%" (
  echo.
  echo Installing this app's dependencies. Needs internet, takes a few minutes
  echo the first time, and changes nothing outside its own environment.
  echo.
  "%VENV_PY%" -m pip install -r requirements.txt || goto :setup_failed
  REM `echo %CUR%` must end the line. Written as `( >"%MARKER%" echo %CUR% )` the
  REM space before the paren lands in the file, so the stored hash never equalled
  REM the computed one and this guard re-ran pip on every single launch.
  if defined CUR (
    >"%MARKER%" echo %CUR%
  )
  echo.
  echo Setup complete.
  echo.
)

"%VENV_PY%" qf_ui.py
if errorlevel 7 if not errorlevel 8 (
  REM Exit code 7 means a self-update is staged. The app has already exited,
  REM so this is the one moment nothing in the folder is open -- hand off to
  REM a copy in %TEMP% so it can replace this very folder, then get out of
  REM its way immediately (no pause, no /wait) so it isn't holding a handle.
  copy /y "%~dp0api\scripts\apply_update.cmd" "%TEMP%\ce_apply_update.cmd" >nul
  start "" "%TEMP%\ce_apply_update.cmd" "%~dp0"
  exit /b
)
pause
exit /b

:setup_failed
echo.
echo Setup failed - check your internet connection and try again.
echo If it keeps failing, double-click "Repair.bat" beside this file.
pause
exit /b 1

:python_missing
echo.
echo Windows Package Manager (winget) is not available, so Python could not be installed automatically.
echo Install Python 3.13 or 3.14 for Windows x64 from https://www.python.org/downloads/ and run this again.
pause
exit /b 1

:python_install_failed
echo.
echo Python could not be installed automatically. Check your internet connection and try again.
pause
exit /b 1

REM --- Candidate interpreter test ------------------------------------------------
REM %1 is an interpreter (a name on PATH or a full path), %2 an optional version
REM selector. A command that does not exist, a `py` selector with no matching
REM runtime, and an untested Python version/architecture all end the same way: the probe
REM exits nonzero, `||` catches it, and the caller moves on to the next candidate.
:use_python_if_supported
"%~1" %2 -c "import platform, sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 13), (3, 14)) and platform.machine().lower() in ('amd64', 'x86_64') else 1)" >nul 2>&1 || exit /b 0
set PY_CMD="%~1"
set "PY_ARGS=%~2"
exit /b 0
