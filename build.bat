@echo off
echo ========================================
echo   DeepSeekChat 构建脚本 (Windows)
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo   下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo [OK] Python %%v

REM 检查 Tkinter
python -c "import tkinter" >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] Tkinter 未安装，GUI 不可用
    echo   请重新安装 Python 时勾选 "tcl/tk and IDLE"
)

REM 安装依赖
echo.
echo [1/3] 安装依赖...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

REM 清理
echo [2/3] 清理旧文件...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM 打包
echo [3/3] 打包为 EXE...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name DeepSeekChat ^
    --clean ^
    --hidden-import tkinter ^
    --hidden-import tkinter.ttk ^
    --hidden-import tkinter.font ^
    --hidden-import tkinter.filedialog ^
    --hidden-import tkinter.messagebox ^
    DeepSeekChat.py

if %errorlevel% neq 0 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo   构建完成！
echo   EXE 文件: dist\DeepSeekChat.exe
echo ========================================
pause
