@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYEXE="
set "PYWEXE="

for %%C in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
) do (
  if not defined PYEXE if exist %%C (
    "%%~C" -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
      set "PYEXE=%%~C"
      set "PYWEXE=%%~dpnCw.exe"
    )
  )
)

if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE (
      "%%~P" -c "import tkinter" >nul 2>&1
      if not errorlevel 1 (
        set "PYEXE=%%~P"
        set "PYWEXE=%%~dpnPw.exe"
      )
    )
  )
)

if not defined PYEXE (
  echo.
  echo [MMY] 未找到带 tkinter 的 Python 解释器，无法启动图形模式。
  echo       请安装官方 Python（勾选 tcl/tk）后重试，或改用命令行：
  echo         python migrate.py ^<profile.zip^> ^<blender.exe^>
  echo.
  pause
  exit /b 1
)

if /i "%~1"=="--check" (
  echo [MMY] 图形模式解释器：!PYWEXE!
  echo [MMY] 控制台解释器：!PYEXE!
  exit /b 0
)

if "%~1"=="" (
  start "" "!PYWEXE!" "%~dp0migrate.py"
) else (
  set "PYTHONIOENCODING=gbk"
  "!PYEXE!" "%~dp0migrate.py" %*
  if errorlevel 1 pause
)

endlocal
