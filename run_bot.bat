@echo off
REM ============================================================
REM  Chay CoC Bot — double-click file nay la xong.
REM
REM  Lan dau se hoi cai dependencies (vai phut). Nhung lan sau
REM  mo thang, khong cai lai.
REM
REM  Khong can go lenh gi. Khong can biet Python.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title CoC Bot

echo.
echo   ============================================
echo      CoC Bot
echo   ============================================
echo.

REM ── 1. Tim Python ───────────────────────────────────────────
REM  Uu tien venv cua project; neu chua co thi dung Python he
REM  thong de tao. "py" la launcher chuan tren Windows, con
REM  "python" co the la ban gia cua Microsoft Store.
set "PYTHON="
if exist "venv\Scripts\python.exe" (
    set "PYTHON=venv\Scripts\python.exe"
    goto :have_python
)

where py >nul 2>&1 && set "PYTHON=py"
if not defined PYTHON (
    where python >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON (
    echo   [LOI] Khong tim thay Python tren may nay.
    echo.
    echo   Tai ve tai:  https://www.python.org/downloads/
    echo   LUU Y: khi cai nho tich o "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

REM ── 2. Tao venv lan dau ─────────────────────────────────────
REM  Cai vao venv rieng thay vi Python he thong, de bot khong
REM  lam hong thu vien cua phan mem khac tren may.
echo   [1/2] Lan dau chay — dang chuan bi moi truong...
echo         (chi lam mot lan, cac lan sau se bo qua buoc nay)
echo.
%PYTHON% -m venv venv
if errorlevel 1 (
    echo   [LOI] Tao moi truong ao that bai.
    pause
    exit /b 1
)
set "PYTHON=venv\Scripts\python.exe"

%PYTHON% -m pip install --upgrade pip --quiet
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [LOI] Cai dat thu vien that bai. Kiem tra ket noi mang.
    echo   Neu van loi, xoa thu muc "venv" roi chay lai file nay.
    pause
    exit /b 1
)
echo.
echo   Chuan bi xong.
echo.

:have_python

REM ── 3. Kiem tra ADB va thiet bi ─────────────────────────────
REM  Canh bao truoc khi mo UI, vi khong co thiet bi thi bot mo
REM  len van chay nhung khong bam duoc gi — de tuong bot hong.
if not exist "2adb.exe" (
    echo   [CANH BAO] Khong thay 2adb.exe canh file nay.
    echo   Bot se mo duoc nhung khong dieu khien duoc may ao.
    echo.
)

if exist "2adb.exe" (
    2adb.exe start-server >nul 2>&1
    for /f "skip=1 tokens=1" %%d in ('2adb.exe devices') do (
        if not "%%d"=="" set "DEVICE=%%d"
    )
    if not defined DEVICE (
        echo   [CANH BAO] Chua co thiet bi nao ket noi.
        echo.
        echo   Mo LDPlayer len truoc, roi chay lai file nay.
        echo   Neu LDPlayer da mo ma van bao loi: vao Settings cua
        echo   LDPlayer, bat "ADB debugging".
        echo.
        choice /c YN /m "   Van mo bot? (Y=co, N=thoat)"
        if errorlevel 2 exit /b 0
        echo.
    ) else (
        echo   Thiet bi: !DEVICE!
        echo.
    )
)

REM ── 4. Chay ─────────────────────────────────────────────────
echo   [2/2] Dang mo bot...
echo.
%PYTHON% main.py

REM  Loi thi giu cua so lai de con doc duoc thong bao; chay
REM  binh thuong xong thi dong luon.
if errorlevel 1 (
    echo.
    echo   ============================================
    echo     Bot da dung vi loi. Noi dung loi o tren.
    echo   ============================================
    echo.
    pause
)
