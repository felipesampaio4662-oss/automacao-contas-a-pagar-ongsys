@echo off
setlocal
cd /d "%~dp0"
title Instalar automacao Ongsys
mode con: cols=100 lines=40

echo ====================================================================
echo  INSTALACAO DA AUTOMACAO ONGSYS
echo ====================================================================
echo.
echo Pasta: %CD%
echo.
echo Este passo baixa o navegador de automacao e pode demorar alguns
echo minutos na primeira vez. Deixe a janela aberta ate o fim.
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERRO: o Python nao foi encontrado neste computador.
  echo.
  echo Instale o Python pelo site python.org e marque a opcao
  echo "Add Python to PATH" durante a instalacao. Depois rode este
  echo arquivo de novo.
  echo.
  pause
  exit /b 1
)

if not exist "automacao_ongsys.py" (
  echo ERRO: o arquivo automacao_ongsys.py nao esta nesta pasta.
  echo Extraia TODO o conteudo do ZIP antes de instalar.
  pause
  exit /b 1
)

if not exist "config.json" (
  echo Criando config.json a partir do modelo...
  copy /y "config.example.json" "config.json" >nul
)

echo [1/4] Criando o ambiente isolado...
python -m venv .venv
if errorlevel 1 goto :erro

echo [2/4] Atualizando o instalador de pacotes...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if errorlevel 1 goto :erro

echo [3/4] Instalando as bibliotecas...
.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 goto :erro

echo [4/4] Instalando o navegador de automacao...
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto :erro

echo.
echo Conferindo a instalacao...
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 (
  echo.
  echo AVISO: alguns testes internos falharam. Anote a tela e peca ajuda
  echo antes de usar o modo executar.
)

echo.
echo ====================================================================
echo  INSTALACAO CONCLUIDA
echo ====================================================================
echo.
echo Proximo passo: de dois cliques em login.bat e faca o login no Ongsys.
echo Depois use planejar.bat para conferir sem lancar nada.
echo.
pause
exit /b 0

:erro
echo.
echo ====================================================================
echo  A INSTALACAO FOI INTERROMPIDA POR UM ERRO
echo ====================================================================
echo Tire uma foto desta tela ao pedir ajuda.
pause
exit /b 1
