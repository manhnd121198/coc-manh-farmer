# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec cho CoC Bot.
Chạy trên Windows:  pyinstaller coc_bot.spec  (hoặc dùng build_exe.bat)

Chế độ --onedir: tạo thư mục dist/CoC-Bot/ chứa CoC-Bot.exe + thư viện.
Các file chạy theo đường dẫn tương đối (2adb.exe, assets, config, ...) KHÔNG
được nhúng vào exe — build_exe.bat sẽ copy chúng cạnh exe sau khi build.
"""

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Gom trọn các thư viện nặng có kèm data/model/DLL riêng.
for pkg in ("easyocr", "torch", "torchvision", "cv2", "skimage", "scipy",
            "Pillow", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # torchvision/skimage/scipy có thể không được cài -> bỏ qua an toàn.
        pass

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ['torch'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CoC-Bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # True nếu muốn thấy cửa sổ log terminal khi chạy
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='assets/icon.ico',  # bỏ comment nếu có icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='CoC-Bot',
)
