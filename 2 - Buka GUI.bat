@echo off
setlocal
REM ============================================================
REM   LANGKAH 2 - Membuka aplikasi P^&ID Studio
REM   Dobel-klik file ini setiap kali ingin memakai aplikasi.
REM   Biarkan jendela hitam ini terbuka selama aplikasi dipakai.
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
REM Hindari konflik OpenMP pada Torch di beberapa Windows.
set "OMP_NUM_THREADS=1"
set "KMP_DUPLICATE_LIB_OK=TRUE"
echo Membuka P^&ID Studio... (tunggu 10-30 detik, jendela aplikasi akan muncul)
echo.

py -3.13 --version >nul 2>&1
if errorlevel 1 (
    echo [GAGAL] Python 3.13 belum terpasang.
    echo Jalankan dulu "1 - Install (jalankan sekali).bat".
    pause
    exit /b 1
)

py -3.13 gui.py

echo.
echo ==== Aplikasi ditutup ====
echo Kalau ADA tulisan error merah/panjang di atas, screenshot layar ini dan kirim ke Razi.
pause
