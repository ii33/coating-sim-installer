@echo off
setlocal DisableDelayedExpansion
rem The packaged installer is the normal Windows entry. No script host or policy change.
set "COAT_INSTALLER="
for %%F in ("%~dp0CoatingSimInstaller-*-windows-x64.exe" "%~dp0dist\CoatingSimInstaller-*-windows-x64.exe") do if exist "%%~fF" if not defined COAT_INSTALLER set "COAT_INSTALLER=%%~fF"
if defined COAT_INSTALLER if "%~1"=="" goto packaged
if defined COAT_INSTALLER if /I "%~1"=="-Gui" if "%~2"=="" goto packaged
if defined COAT_INSTALLER if /I "%~1"=="--project" goto packaged_args
if defined COAT_INSTALLER if /I "%~1"=="--self-check" goto packaged_args
if defined COAT_INSTALLER if /I "%~1"=="--smoke-gui" goto packaged_args
if defined COAT_INSTALLER if /I "%~1"=="--smoke-outcome" goto packaged_args
if defined COAT_INSTALLER if /I "%~1"=="--version" goto packaged_args
rem Advanced source CLI needs an explicit or already project-private interpreter.
set "COAT_PYTHON=%COAT_DEPENDENCY_PYTHON%"
if not defined COAT_PYTHON if exist "%~dp0.runtime\python\python.exe" set "COAT_PYTHON=%~dp0.runtime\python\python.exe"
if not defined COAT_PYTHON (
  >&2 echo Source CLI requires COAT_DEPENDENCY_PYTHON or an existing private runtime.
  >&2 echo Normal Windows installation uses the packaged CoatingSimInstaller EXE.
  >&2 echo No PowerShell script or execution-policy change was attempted.
  exit /b 2
)
if not exist "%COAT_PYTHON%" (
  >&2 echo Selected Python executable does not exist.
  exit /b 2
)
"%COAT_PYTHON%" -I -S -B "%~dp0tools\dependency_manager\source_entry.py" %*
exit /b %ERRORLEVEL%
:packaged
"%COAT_INSTALLER%"
exit /b %ERRORLEVEL%
:packaged_args
"%COAT_INSTALLER%" %*
exit /b %ERRORLEVEL%
