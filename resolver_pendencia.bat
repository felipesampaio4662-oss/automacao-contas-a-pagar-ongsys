@echo off
setlocal
cd /d "%~dp0"
title Resolver pendencia do checkpoint - nao lanca nada
mode con: cols=115 lines=48

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
echo  RESOLVER PENDENCIA DO CHECKPOINT - NAO LANCAR
echo ====================================================================
echo.
echo Este modo NAO abre o Ongsys e NAO cria, altera, paga ou baixa titulos.
echo Ele apenas libera no checkpoint uma tentativa que voce ja confirmou
echo manualmente como NAO existente no Ongsys.
echo.
echo Antes de continuar, tenha certeza de que pesquisou fornecedor,
echo documento, valor e datas no Ongsys.
echo.

%PY% automacao_ongsys.py resolver-pendencia
echo.
pause
