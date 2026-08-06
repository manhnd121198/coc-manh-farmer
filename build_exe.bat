@echo off
REM ============================================================
REM  Build CoC Bot thanh file .exe (chi chay tren WINDOWS)
REM  Cach dung: double-click file nay, hoac chay trong terminal:
REM      build_exe.bat
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo [1/5] Kich hoat moi truong ao (neu co)...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo   Khong tim thay venv, dung Python he thong.
)

echo.
echo [2/5] Cai dat dependencies + PyInstaller...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo   [LOI] Cai dat that bai. Dung lai.
    pause
    exit /b 1
)

echo.
echo [3/5] Xoa ban build cu...
if exist "build" rmdir /s /q "build"
if exist "dist\CoC-Bot" rmdir /s /q "dist\CoC-Bot"

echo.
echo [4/5] Build bang PyInstaller (co the mat vai phut)...
python -m PyInstaller coc_bot.spec --noconfirm
if errorlevel 1 (
    echo   [LOI] Build that bai. Xem log ben tren.
    pause
    exit /b 1
)

echo.
echo [5/5] Copy file runtime (chay theo duong dan tuong doi) canh exe...
set "OUT=dist\CoC-Bot"
copy /y "2adb.exe"          "%OUT%\" >nul 2>&1
copy /y "AdbWinApi.dll"     "%OUT%\" >nul 2>&1
copy /y "AdbWinUsbApi.dll"  "%OUT%\" >nul 2>&1
xcopy /e /i /y "assets"     "%OUT%\assets"     >nul
xcopy /e /i /y "config"     "%OUT%\config"     >nul
xcopy /e /i /y "profiles"   "%OUT%\profiles"   >nul
xcopy /e /i /y "strategies" "%OUT%\strategies" >nul
if exist "recordings" xcopy /e /i /y "recordings" "%OUT%\recordings" >nul

echo.
echo ============================================================
echo  HOAN TAT!
echo  File chay:  %OUT%\CoC-Bot.exe
echo  Copy CA THU MUC "%OUT%" khi mang sang may khac.
echo  (Lan chay dau EasyOCR se tai model -> can Internet.)
echo ============================================================
echo.
pause
