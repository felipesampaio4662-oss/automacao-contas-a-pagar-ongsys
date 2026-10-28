@echo off
setlocal
cd /d "%~dp0"
title Situacao dos lancamentos

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo A automacao ainda nao foi instalada nesta pasta.
  echo De dois cliques em instalar.bat e espere terminar.
  echo.
  pause
  exit /b 1
)
set "PY=.venv\Scripts\python.exe"

%PY% automacao_ongsys.py estado
echo.
pause
