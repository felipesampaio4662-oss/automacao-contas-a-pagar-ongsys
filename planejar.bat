@echo off
setlocal
cd /d "%~dp0"
title Conferir lancamentos (nao altera o Ongsys)
mode con: cols=110 lines=45

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
echo  CONFERENCIA - MODO SEGURO
echo ====================================================================
echo.
echo Este modo apenas LE a planilha e gera relatorios.
echo Ele NAO abre o Ongsys e NAO cria nenhum lancamento.
echo.

%PY% automacao_ongsys.py planejar
echo.
echo Abra a pasta "relatorios" para conferir os arquivos gerados.
echo.
pause
