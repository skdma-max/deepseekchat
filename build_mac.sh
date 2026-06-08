#!/bin/bash
# ========================================
#   DeepSeekChat 构建脚本 — macOS
# ========================================
set -e

echo "========================================"
echo "  DeepSeekChat 构建脚本 (macOS)"
echo "========================================"
echo

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "[错误] 未找到 Python3"
    echo "  请安装: brew install python@3.12 python-tk@3.12"
    exit 1
fi

PYTHON=python3
echo "[✓] Python: $($PYTHON --version)"

# 检查 Tkinter
if ! $PYTHON -c "import tkinter" &>/dev/null; then
    echo "[警告] Tkinter 未安装"
    echo "  请通过 Homebrew 安装: brew install python-tk@3.12"
    echo "  或使用 python.org 官方安装包 (预装 Tkinter)"
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

# 打包为 .app bundle (带图标)
echo "[3/3] 打包为 .app 应用程序..."
$PYTHON -m PyInstaller \
    --onefile \
    --windowed \
    --name DeepSeekChat \
    --clean \
    --osx-bundle-identifier com.deepseekchat.app \
    --add-data "icon.png:." 2>/dev/null || true
echo "[✓] 打包完成"

echo
echo "========================================"
echo "  构建完成！"
echo "  可执行文件: dist/DeepSeekChat"
echo "  应用程序包: dist/DeepSeekChat.app (若启用了 --onedir 会生成)"
echo ""
echo "  若要打包为 .dmg:"
echo "    npm install -g create-dmg"
echo "    create-dmg dist/DeepSeekChat.app dist/"
echo "========================================"
