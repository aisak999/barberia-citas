@echo off
title Barberia - citas
cd /d "%~dp0"
if not exist .venv (
  echo Preparando por primera vez, espera un momento...
  py -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
echo.
echo  ==============================================
echo   Clientes:  http://localhost:5000
echo   Barbero:   http://localhost:5000/panel   (clave: barbero123)
echo   Para cerrar: cierra esta ventana
echo  ==============================================
echo.
start "" http://localhost:5000
python app.py
pause
