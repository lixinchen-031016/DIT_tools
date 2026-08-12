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
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== [1/5] 检查环境 ==="
if [[ "$(uname)" != "Darwin" ]]; then
    echo "错误：此脚本仅在 macOS 上运行"
    exit 1
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "错误：找不到 python3，请先安装 Python 3"
    exit 1
fi

if ! command -v mediainfo >/dev/null 2>&1; then
    echo "警告：未安装 MediaInfo，视频元数据功能将受限"
    echo "      请运行：brew install mediainfo"
fi

echo "=== [2/5] 创建/复用 venv ==="
if [[ ! -d ".venv" ]]; then
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "=== [3/5] 安装依赖 ==="
python -m pip install --upgrade pip >/dev/null
# rawpy 为可选依赖（部分平台无预编译 wheel），从主依赖列表单独过滤安装
TMP_REQ="${TMPDIR:-/tmp}/dit-requirements-core.txt"
grep -v '^rawpy' requirements.txt > "$TMP_REQ"
python -m pip install -r "$TMP_REQ" >/dev/null
rm -f "$TMP_REQ"
if ! python -m pip install "rawpy>=0.21.0" >/dev/null; then
    echo "警告：rawpy 安装失败（可选），RAW 缩略图将降级为 EXIF 内嵌预览"
fi
python -m pip install pyinstaller >/dev/null

echo "=== [4/5] 运行单元测试 ==="
cd DITWorkstation
python -m pytest DITWorkstationTests/ -q
cd "$PROJECT_ROOT"

echo "=== [5/5] 执行 PyInstaller 打包 ==="
rm -rf build/dist dist 2>/dev/null || true
pyinstaller build/DITWorkstation.spec --noconfirm --clean

echo ""
echo "=== 打包完成 ==="
echo "产物路径: $(pwd)/dist/DITWorkstation.app"
echo "启动应用: open dist/DITWorkstation.app"
echo ""
echo "数据目录（运行时）: ~/Library/Application Support/DITWorkstation/"
