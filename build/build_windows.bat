@echo off
REM DITWorkstation Windows 打包脚本
REM
REM 前置条件：
REM   1. Windows 主机（PyInstaller 不支持交叉编译）
REM   2. Python 3.11+ 已安装并加入 PATH
REM   3. 已安装 MediaInfo: https://mediaarea.net/en/MediaInfo
REM
REM 用法：
REM   cd DIT_tools
REM   build\build_windows.bat
REM
REM 产物：
REM   dist\DITWorkstation\DITWorkstation.exe

setlocal enabledelayedexpansion

cd /d "%~dp0\.."

echo === [1/5] 检查环境 ===
where python >nul 2>&1
if errorlevel 1 (
    echo 错误：找不到 python，请先安装 Python 3 并加入 PATH
    exit /b 1
)

where mediainfo >nul 2>&1
if errorlevel 1 (
    echo 警告：未安装 MediaInfo，视频元数据功能将受限
    echo       请从 https://mediaarea.net/en/MediaInfo 下载安装
)

echo === [2/5] 创建/复用 venv ===
if not exist ".venv" (
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo === [3/5] 安装依赖 ===
python -m pip install --upgrade pip >nul
REM rawpy 为可选依赖（部分平台无预编译 wheel），从主依赖列表单独过滤安装
findstr /v /b rawpy requirements.txt > requirements-core.txt
python -m pip install -r requirements-core.txt >nul
del requirements-core.txt
python -m pip install "rawpy>=0.21.0" >nul
if errorlevel 1 (
    echo 警告：rawpy 安装失败（可选），RAW 缩略图将降级为 EXIF 内嵌预览
)
python -m pip install pyinstaller >nul

echo === [4/5] 运行单元测试 ===
cd DITWorkstation
python -m pytest DITWorkstationTests/ -q
if errorlevel 1 (
    echo 错误：单元测试失败，打包中止
    exit /b 1
)
cd /d "%~dp0\.."

echo === [5/5] 执行 PyInstaller 打包 ===
if exist dist rmdir /s /q dist
if exist build\dist rmdir /s /q build\dist
pyinstaller build\DITWorkstation.spec --noconfirm --clean
if errorlevel 1 (
    echo 错误：打包失败
    exit /b 1
)

echo.
echo === 打包完成 ===
echo 产物路径: %CD%\dist\DITWorkstation\DITWorkstation.exe
echo 启动应用: dist\DITWorkstation\DITWorkstation.exe
echo.
echo 数据目录（运行时）: %%APPDATA%%\DITWorkstation\
endlocal
