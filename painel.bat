@echo off
setlocal
cd /d "%~dp0"
title Painel da Automacao Ongsys

if not exist ".venv\Scripts\pythonw.exe" (
  echo.
  echo A automacao ainda nao foi instalada nesta pasta.
  echo De dois cliques em instalar.bat e espere terminar.
  echo.
  pause
  exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "painel_ongsys.py"
