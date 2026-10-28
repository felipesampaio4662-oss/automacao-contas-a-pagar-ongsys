@echo off
setlocal
cd /d "%~dp0"
title Login manual no Ongsys

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
echo  LOGIN NO ONGSYS
echo ====================================================================
echo.
echo O navegador vai abrir. Faca o login normalmente, com seu usuario e
echo sua senha, direto na tela do Ongsys.
echo.
echo O programa NAO ve e NAO guarda a sua senha. So a sessao do navegador
echo fica salva, apenas neste computador, na pasta perfil_ongsys.
echo.
pause

%PY% automacao_ongsys.py login
echo.
pause
