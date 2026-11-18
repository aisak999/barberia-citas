@echo off
title Barberia - MODO PRUEBA
cd /d "%~dp0"
if not exist .venv (
  echo Preparando por primera vez, espera un momento...
  py -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

rem Modo prueba: usa una base aparte (prueba.db) que se borra y se llena con citas de ejemplo cada vez
set BARBERIA_DEMO=1
set BARBERIA_DB=%~dp0prueba.db
if exist prueba.db del prueba.db

start "" http://localhost:5000/panel
python app.py
pause
