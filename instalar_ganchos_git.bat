@echo off
setlocal
cd /d "%~dp0"
title Instalar conferencia de dados pessoais no Git
if not exist ".git" (
  echo Esta pasta nao e um repositorio Git. Nada foi feito.
  pause
  exit /b 1
)
echo Este atalho faz o Git rodar verificar_publicacao.py antes de todo commit
echo e de todo envio ao GitHub. Se encontrar dado pessoal, o Git recusa.
echo.
set /p "OK=Digite SIM para instalar: "
if /i not "%OK%"=="SIM" (
  echo Cancelado. Nada foi instalado.
  pause
  exit /b 0
)
(
  echo #!/bin/sh
  echo exec ".venv/Scripts/python.exe" verificar_publicacao.py
) > ".git\hooks\pre-commit"
(
  echo #!/bin/sh
  echo exec ".venv/Scripts/python.exe" verificar_publicacao.py --commit HEAD
) > ".git\hooks\pre-push"
echo.
echo Pronto. Para desfazer, apague os arquivos pre-commit e pre-push da pasta .git\hooks.
pause
