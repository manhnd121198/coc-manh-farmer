#!/bin/bash
# ============================================================
#  Chay CoC Bot tren macOS — double-click file nay la xong.
#
#  Lan dau se cai dependencies (vai phut). Nhung lan sau mo thang.
#
#  Duoi .command de Finder cho phep double-click. Neu macOS bao
#  "khong co quyen", mo Terminal tai thu muc nay va chay:
#      chmod +x run_bot.command
# ============================================================
cd "$(dirname "$0")" || exit 1

echo
echo "  ============================================"
echo "     CoC Bot"
echo "  ============================================"
echo

# ── 1. Tim Python ───────────────────────────────────────────
# Uu tien venv cua project. macOS khong con python2, nhung
# "python" van co the tro toi ban Xcode thieu thu vien, nen
# tim python3 truoc.
if [ -x "venv/bin/python3" ]; then
    PYTHON="venv/bin/python3"
    HAVE_VENV=1
else
    PYTHON="$(command -v python3 || command -v python)"
    HAVE_VENV=0
fi

if [ -z "$PYTHON" ]; then
    echo "  [LOI] Khong tim thay Python tren may nay."
    echo
    echo "  Cai bang Homebrew:  brew install python"
    echo "  Hoac tai tai:       https://www.python.org/downloads/"
    echo
    read -r -p "  Nhan Enter de dong..."
    exit 1
fi

# ── 2. Tao venv lan dau ─────────────────────────────────────
# Cai vao venv rieng thay vi Python he thong: macOS chan ghi
# vao python he thong, va lam vay cung khong dung toi thu vien
# cua phan mem khac tren may.
if [ "$HAVE_VENV" -eq 0 ]; then
    echo "  [1/2] Lan dau chay — dang chuan bi moi truong..."
    echo "        (chi lam mot lan, cac lan sau se bo qua buoc nay)"
    echo
    "$PYTHON" -m venv venv || {
        echo "  [LOI] Tao moi truong ao that bai."
        read -r -p "  Nhan Enter de dong..."
        exit 1
    }
    PYTHON="venv/bin/python3"
    "$PYTHON" -m pip install --upgrade pip --quiet
    if ! "$PYTHON" -m pip install -r requirements.txt; then
        echo
        echo "  [LOI] Cai dat thu vien that bai. Kiem tra ket noi mang."
        echo "  Neu van loi, xoa thu muc \"venv\" roi chay lai file nay."
        read -r -p "  Nhan Enter de dong..."
        exit 1
    fi
    echo
    echo "  Chuan bi xong."
    echo
fi

# ── 3. ADB ──────────────────────────────────────────────────
# Khong kiem tra thiet bi da ket noi hay chua — may Mac cam
# dien thoai that, cam rut lien tuc, chan o day chi lam vuong.
if ! command -v adb >/dev/null 2>&1 && [ -z "$COC_ADB_PATH" ]; then
    echo "  [CANH BAO] Khong thay 'adb' tren PATH."
    echo "  Bot van mo duoc nhung khong dieu khien duoc thiet bi."
    echo
    echo "  Cai:  brew install android-platform-tools"
    echo "  Hoac: export COC_ADB_PATH=/duong/dan/toi/adb"
    echo
fi

# ── 4. Chay ─────────────────────────────────────────────────
echo "  [2/2] Dang mo bot..."
echo
"$PYTHON" main.py
STATUS=$?

# Loi thi giu cua so lai de con doc duoc thong bao.
if [ $STATUS -ne 0 ]; then
    echo
    echo "  ============================================"
    echo "    Bot da dung vi loi. Noi dung loi o tren."
    echo "  ============================================"
    echo
    read -r -p "  Nhan Enter de dong..."
fi
