@echo off
setlocal
REM ============================================================
REM   LANGKAH 1 - Pemasangan (cukup dijalankan SATU KALI)
REM   Dobel-klik file ini. Butuh koneksi internet.
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
echo.
echo ==========================================================
echo   Memasang komponen P^&ID Studio...
echo   Proses ini sekali saja dan bisa memakan 5-15 menit
echo   (mengunduh torch, ultralytics, dll). Mohon ditunggu.
echo ==========================================================
echo.

REM Python 3.14 belum didukung penuh oleh dependency proyek.
set "PYTHON_CMD="
py -3.13 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.13"

if not defined PYTHON_CMD (
    echo Python 3.13 belum ditemukan. Mencoba memasangnya melalui winget...
    winget install --id Python.Python.3.13 -e --scope user --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo [GAGAL] Python 3.13 tidak dapat dipasang otomatis.
        echo Pasang Python 3.13 dari https://www.python.org/downloads/
        echo Setelah itu jalankan ulang file ini.
        echo.
        pause
        exit /b 1
    )
    py -3.13 --version >nul 2>&1
    if errorlevel 1 (
        echo [GAGAL] Python 3.13 belum terdeteksi setelah pemasangan.
        echo Tutup dan buka kembali Command Prompt, lalu jalankan ulang file ini.
        echo.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=py -3.13"
)

echo Menggunakan:
%PYTHON_CMD% --version
echo.

%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 goto install_failed
%PYTHON_CMD% -m pip install --prefer-binary -r requirements.txt
if errorlevel 1 goto install_failed

echo.
echo ==========================================================
echo   [SELESAI] Pemasangan berhasil.
echo   Sekarang jalankan  "2 - Buka GUI.bat"
echo ==========================================================
echo.
pause
exit /b 0

:install_failed
echo.
echo ==========================================================
echo   [GAGAL] Ada masalah saat pemasangan dependency.
echo   Baca error di atas untuk mengetahui penyebabnya.
echo ==========================================================
echo.
pause
exit /b 1
