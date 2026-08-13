# -*- mode: python ; coding: utf-8 -*-
"""
DITWorkstation PyInstaller 打包配置

用法：
    cd DIT_tools/
    pyinstaller build/DITWorkstation.spec --noconfirm

产物（单应用程序）：
    macOS:   dist/DITWorkstation.app  （onedir 模式，依赖放入 Contents/Frameworks）
    Windows: dist/DITWorkstation.exe   （单文件可执行）
"""
import os
import sys
import platform
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 项目根目录（spec 文件位于 build/ 下，所以根目录是上一层）
PROJECT_ROOT = Path(SPECPATH).parent
ENTRY_SCRIPT = str(PROJECT_ROOT / "DITWorkstation" / "DITWorkstation" / "main.py")


def _collect_mediainfo_binaries():
    """按平台收集 libmediainfo 动态库，随包分发"""
    binaries = []
    system = platform.system()
    candidates = []
    if system == "Darwin":
        candidates = [
            "/opt/homebrew/lib/libmediainfo.0.dylib",
            "/opt/homebrew/lib/libmediainfo.dylib",
            "/usr/local/lib/libmediainfo.0.dylib",
            "/usr/local/lib/libmediainfo.dylib",
        ]
    elif system == "Windows":
        configured = os.environ.get("MEDIAINFO_DLL")
        if configured:
            candidates.append(configured)
        pf64 = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
        pf32 = os.environ.get("ProgramFiles(x86)")
        if pf64:
            candidates.append(os.path.join(pf64, "MediaInfo", "MediaInfo.dll"))
        if pf32:
            candidates.append(os.path.join(pf32, "MediaInfo", "MediaInfo.dll"))
        candidates.extend([
            r"C:\Program Files\MediaInfo\MediaInfo.dll",
            r"C:\Program Files (x86)\MediaInfo\MediaInfo.dll",
        ])
    for c in candidates:
        if os.path.exists(c):
            binaries.append((c, "."))
            print(f"[spec] bundled MediaInfo lib: {c}")
            break
    else:
        print("[spec] warning: MediaInfo lib not found, video metadata will be limited")
    return binaries


hiddenimports = [
    # PySide6 模块
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    # 第三方依赖
    "exifread",
    "PIL",
    "PIL._tkinter_finder",
    "pymediainfo",
    "xxhash",
    "reportlab",
]
# reportlab 的标准 14 字体宽度表与编码表是按需动态 import 的子模块
# （reportlab 5.0 起模块名变化，逐个列举易失效），整体收集最稳妥
hiddenimports += collect_submodules("reportlab.pdfbase")


a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[str(PROJECT_ROOT / "DITWorkstation")],
    binaries=_collect_mediainfo_binaries(),
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == "darwin":
    # macOS: onedir 模式（PyInstaller 6.x 推荐，onefile+.app 已被弃用）
    # 可执行文件在 Contents/MacOS，依赖与数据在 Contents/Frameworks；
    # 运行时 sys._MEIPASS 指向 Contents/Frameworks（MediaInfo 查找依赖此路径）
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="DITWorkstation",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,  # GUI 应用，不显示终端
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity="-",  # adhoc 签名 + entitlements
        entitlements_file=str(Path(SPECPATH) / "DITWorkstation.entitlements.plist"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="DITWorkstation",
    )
    app = BUNDLE(
        coll,
        name="DITWorkstation.app",
        icon=None,  # TODO: 提供 icon.icns 后填入路径
        bundle_identifier="com.ditworkstation.app",
        info_plist={
            "CFBundleName": "DIT工作站",
            "CFBundleDisplayName": "DIT工作站",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": "DIT工作站需要访问文件以管理媒体素材。",
            "NSPhotoLibraryUsageDescription": "DIT工作站需要访问照片库以管理摄影素材。",
        },
    )
elif sys.platform == "win32":
    # Windows: 生成单文件可执行程序，带 longPathAware manifest（支持 >260 字符路径）
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="DITWorkstation",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,  # GUI 应用
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        manifest=str(Path(SPECPATH) / "DITWorkstation.manifest"),
    )
else:
    # 其他平台（Linux 等）：生成单文件可执行程序
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="DITWorkstation",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,  # GUI 应用
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
