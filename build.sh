#!/bin/bash
# ========================================
#   DeepSeekChat 构建脚本 — Linux
# ========================================
set -e

echo "========================================"
echo "  DeepSeekChat 构建脚本 (Linux)"
echo "========================================"
echo

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "[错误] 未找到 Python3，请先安装 Python 3.10+"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-tk"
    echo "  Fedora:        sudo dnf install python3 python3-pip python3-tkinter"
    echo "  Arch:          sudo pacman -S python python-pip tk"
    exit 1
fi

PYTHON=python3
echo "[✓] Python: $($PYTHON --version)"

# 检查 Tkinter
if ! $PYTHON -c "import tkinter" &>/dev/null; then
    echo "[警告] Tkinter 未安装，GUI 将不可用"
    echo "  请安装: sudo apt install python3-tk (Debian/Ubuntu)"
    echo "          sudo dnf install python3-tkinter (Fedora)"
fi

# 安装依赖
echo
echo "[1/3] 安装依赖..."
$PYTHON -m pip install -r requirements.txt
echo "[✓] 依赖安装完成"

# 清理
echo "[2/3] 清理旧文件..."
rm -rf dist build
echo "[✓] 清理完成"

# 打包
echo "[3/3] 打包为可执行文件..."
$PYTHON -m PyInstaller \
    --onefile \
    --windowed \
    --name DeepSeekChat \
    --clean \
    --add-data "icon.png:." 2>/dev/null || true
echo "[✓] 打包完成"

echo
echo "========================================"
echo "  构建完成！"
echo "  可执行文件: dist/DeepSeekChat"
echo ""
echo "  提示: 首次运行可能需要 xdg-open 权限"
echo "========================================"
