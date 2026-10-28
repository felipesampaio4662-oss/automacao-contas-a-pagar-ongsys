@echo off
setlocal
cd /d "%~dp0"
title Conferir dados pessoais antes de enviar ao GitHub
if not exist ".venv\Scripts\python.exe" (
  echo A automacao ainda nao foi instalada nesta pasta. Rode instalar.bat.
  pause
  exit /b 1
)
echo Conferindo os arquivos que iriam para o GitHub (nada e enviado)...
echo.
.venv\Scripts\python.exe verificar_publicacao.py
echo.
pause
