@echo off
setlocal
cd /d "%~dp0"
title Limpar arquivos antigos da automacao
mode con: cols=110 lines=40

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
echo  LIMPAR CAPTURAS, RELATORIOS E COPIAS ANTIGAS
echo ====================================================================
echo.
echo Esses arquivos guardam nomes, CPF/CNPJ e valores. Eles servem para
echo conferir as execucoes recentes e nao precisam ficar para sempre.
echo.
echo O programa mostra primeiro o que passou do prazo e so apaga se voce
echo digitar APAGAR. Ele nao abre o Ongsys e nao mexe na memoria de
echo lancamentos, na configuracao nem no login.
echo.

%PY% automacao_ongsys.py limpar
echo.
pause
