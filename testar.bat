@echo off
setlocal
cd /d "%~dp0"
title Testes internos da automacao

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
echo  TESTES INTERNOS
echo ====================================================================
echo.
echo Confere as regras de agrupamento, exclusao, percentuais e codigos.
echo Nao usa o Ongsys e nao usa a internet.
echo.

%PY% -m pytest -v
echo.
pause
