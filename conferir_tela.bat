@echo off
setlocal
cd /d "%~dp0"
title Conferir a tela do Ongsys

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo A automacao ainda nao foi instalada nesta pasta.
  echo De dois cliques em instalar.bat e espere terminar.
  echo.
  pause
  exit /b 1
)
set "PY=.venv\Scripts\python.exe"

echo ====================================================================
echo  CONFERENCIA DA TELA DO ONGSYS
echo ====================================================================
echo.
echo Este modo abre o formulario de inclusao e verifica se os campos
echo continuam com os mesmos nomes internos. Nada e preenchido e nada
echo e salvo.
echo.
echo Use este teste antes do primeiro lancamento e sempre que o Ongsys
echo mudar de aparencia.
echo.
pause

%PY% automacao_ongsys.py conferir-tela
echo.
pause
