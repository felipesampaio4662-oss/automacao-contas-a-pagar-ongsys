@echo off
setlocal
cd /d "%~dp0"
title LANCAR no Ongsys - exige confirmacao
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
echo  LANCAMENTO DE CONTAS A PAGAR
echo ====================================================================
echo.
echo Cria ou atualiza titulos no Ongsys.
echo Nao baixa, nao paga, nao remete e nao envia para aprovacao.
echo Antes de confirmar, confira a planilha e o relatorio de conferencia.
echo.
set /p "SEGUIR=Digite SIM para abrir o modo de lancamento: "
if /i not "%SEGUIR%"=="SIM" (
  echo.
  echo Cancelado. Nada foi enviado ao Ongsys.
  pause
  exit /b 0
)

echo.
echo ----------------------- FILTROS OPCIONAIS ---------------------------
echo Deixe em branco para processar toda a fila.
echo.
set "FORNECEDOR="
set /p "FORNECEDOR=Lancar so um fornecedor especifico (nome ou parte do nome): "
set "LIMITE="
set /p "LIMITE=Limite maximo de titulos nesta execucao (ex.: 1 para o piloto): "

echo.
set "DUPLICAR="
set /p "DUPLICAR=Copia de teste intencional (normal: deixe em branco): "

set "EXTRA="
if not "%FORNECEDOR%"=="" set EXTRA=%EXTRA% --somente-fornecedor "%FORNECEDOR%"
if not "%LIMITE%"=="" set EXTRA=%EXTRA% --limite %LIMITE%
if /i "%DUPLICAR%"=="DUPLICAR" set EXTRA=%EXTRA% --permitir-duplicidade-exata

if not "%EXTRA%"=="" (
  echo.
  echo Rodando com filtros:%EXTRA%
)

%PY% automacao_ongsys.py executar %EXTRA%
echo.
pause
