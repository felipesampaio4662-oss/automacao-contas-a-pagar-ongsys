@echo off
setlocal
cd /d "%~dp0"
title Auditar titulos ja lancados
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
echo  AUDITORIA DE TITULOS JA LANCADOS
echo ====================================================================
echo.
echo Este modo abre cada titulo salvo e compara com a planilha.
echo Ele so LE o Ongsys: nao altera, nao baixa e nao apaga nada.
echo Se um salvamento confirmado ficou sem ID no checkpoint, este modo
echo procura uma correspondencia unica e recupera o ID antes de conferir.
echo.
echo Informe a mesma planilha usada no lancamento, para que a comparacao
echo tenha os valores esperados.
echo.

%PY% automacao_ongsys.py auditar
echo.
pause
