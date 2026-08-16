#!/usr/bin/env bash
# DITWorkstation macOS 打包脚本
#
# 前置条件：
#   1. macOS 主机（PyInstaller 不支持交叉编译）
#   2. Python 3.11+ 已安装
#   3. 已安装 MediaInfo: brew install mediainfo
#
# 用法：
#   cd DIT_tools/
#   bash build/build_macos.sh
#
# 产物：
#   dist/DITWorkstation.app
#   dist/DITWorkstation.dmg（可选，hdiutil 可用时自动生成）
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== [1/6] 检查环境 ==="
if [[ "$(uname)" != "Darwin" ]]; then
    echo "错误：此脚本仅在 macOS 上运行"
    exit 1
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "错误：找不到 $PYTHON，请先安装 Python 3 并加入 PATH"
    exit 1
fi
# Python 版本预检（与 README 要求一致：3.11+）
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "错误：需要 Python 3.11+，当前版本：$("$PYTHON" --version 2>&1)"
    exit 1
fi

if ! command -v mediainfo >/dev/null 2>&1; then
    echo "错误：未安装 MediaInfo。正式构建要求视频元数据依赖完整。"
    echo "      请运行：brew install mediainfo"
    exit 1
fi

echo "=== [2/6] 创建/复用 venv ==="
if [[ ! -d ".venv" ]]; then
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "=== [3/6] 安装依赖 ==="
python -m pip install --upgrade pip >/dev/null
# RAW 与 PyAV 是正式交付依赖；安装失败即中止，避免构建出功能不完整的包。
python -m pip install -r requirements.txt >/dev/null

echo "=== [4/6] 运行单元测试 ==="
cd DITWorkstation
python -m pytest DITWorkstationTests/ -q
cd "$PROJECT_ROOT"

echo "=== [5/6] 执行 PyInstaller 打包 ==="
# 清理旧的 PyInstaller 工作目录与产物（工作目录是 build/DITWorkstation，不是 build/dist）
rm -rf build/DITWorkstation dist 2>/dev/null || true
pyinstaller build/DITWorkstation.spec --noconfirm --clean
# onedir 模式会额外产出 COLLECT 副产品目录 dist/DITWorkstation（与 .app 内容重复），
# 只保留 .app 与 .dmg 作为分发产物
rm -rf dist/DITWorkstation

echo "=== [6/6] 生成 DMG（可选） ==="
if command -v hdiutil >/dev/null 2>&1 && [[ -d "dist/DITWorkstation.app" ]]; then
    if hdiutil create -volname "DIT工作站" -srcfolder "dist/DITWorkstation.app" \
        -ov -format UDZO "dist/DITWorkstation.dmg" >/dev/null; then
        echo "DMG 产物: $(pwd)/dist/DITWorkstation.dmg"
    else
        echo "警告：DMG 生成失败，不影响 .app 产物"
    fi
else
    echo "跳过 DMG 生成（hdiutil 不可用或 .app 缺失）"
fi

echo ""
echo "=== 打包完成 ==="
echo "产物路径: $(pwd)/dist/DITWorkstation.app"
echo "启动应用: open dist/DITWorkstation.app"
echo ""
echo "数据目录（运行时）: ~/Library/Application Support/DITWorkstation/"
