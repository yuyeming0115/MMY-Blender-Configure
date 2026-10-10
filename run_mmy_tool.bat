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
  for /f "delems=" %%P in ('where python 2^>nul') do (
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
  echo [MMY] 未找到带 tkinter 的 Python，无法打开图形界面
  echo        请安装 Python（安装时勾选 tcl/tk 组件），或用命令行运行：
  echo        python migrate.py ^<配置包.zip^> ^<blender.exe^>
  echo.
  pause
  exit /b 1
)

if /i "%~1"=="--check" (
  echo [MMY] 图形模式解释器：!PYEXE!
  "!PYEXE!" -c "import tkinterdnd2" >nul 2>&1
  if not errorlevel 1 (
    echo [MMY] 拖拽支持：可用
  ) else (
    echo [MMY] 拖拽支持：不可用（投放区仍可点击选择，功能不受影响）
  )
  echo [MMY] 自检通过
  exit /b 0
)

start "" "!PYWEXE!" "%~dp0mmy_tool_gui.py"

endlocal