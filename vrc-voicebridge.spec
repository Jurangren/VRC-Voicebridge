# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

构建：  pyinstaller --noconfirm vrc-voicebridge.spec
产物：  dist/VRC-VoiceBridge/VRC-VoiceBridge.exe（带依赖目录）

说明：
- web 模板/静态资源、config.example.json 作为 data 一并打包；
- 重型可选依赖（openvr/faster-whisper/ctranslate2/sherpa-onnx 等）用 collect_all 收集其
  native 库与数据文件，缺失的包用 try/except 跳过，不影响其余功能；
- console=True 便于看到运行日志与报错。
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ("web/templates", "web/templates"),
    ("web/static", "web/static"),
    ("config.example.json", "."),
]
binaries = []
hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "win32timezone",
    "tools.vr_controller_bridge",
]

# 这些包带 native 库或数据文件，需要整包收集；未安装的会被跳过
for _pkg in (
    "openvr",
    "faster_whisper",
    "ctranslate2",
    "sherpa_onnx",
    "onnxruntime",
    "soundcard",
    "av",
    "tokenizers",
    "huggingface_hub",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        pass


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "tensorflow"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VRC-VoiceBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VRC-VoiceBridge",
)
