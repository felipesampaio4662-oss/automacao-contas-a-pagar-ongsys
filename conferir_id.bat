@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Conferir titulo lancado a mao (por ID)
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
echo  CONFERIR TITULO LANCADO A MAO (pelo ID do Ongsys)
echo ====================================================================
echo.
echo Use quando VOCE lancou um titulo manualmente no Ongsys e quer que a
echo automacao confira os dados e marque como concluido no checkpoint,
echo para ele NAO ser lancado de novo na proxima execucao.
echo.
echo Ele so LE o Ongsys: nao altera, nao baixa, nao paga e nao apaga nada.
echo.
echo Onde achar o ID: abra o titulo na lista de Contas a Pagar; o ID e o
echo numero da primeira coluna (ex.: 00013500).
echo.

set "IDS="
set "LISTA="
:pergunta
set "ID="
set /p "ID=Digite um ID e pressione Enter (ou so Enter para terminar): "
if not "!ID!"=="" (
  set "IDS=!IDS! --auditar-id !ID!"
  set "LISTA=!LISTA! !ID!"
  goto pergunta
)

if "!IDS!"=="" (
  echo.
  echo Nenhum ID informado. Nada foi feito.
  echo.
  pause
  exit /b 0
)

echo.
echo IDs que serao conferidos:!LISTA!
echo.
echo Agora informe a MESMA planilha usada no lancamento, para a
echo comparacao ter os valores esperados.
echo.
%PY% automacao_ongsys.py auditar!IDS!
echo.
pause
