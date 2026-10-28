@echo off
setlocal
cd /d "%~dp0"
title Testar busca de fornecedor (nao salva nada)
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
echo  TESTE DE BUSCA DE FORNECEDOR
echo ====================================================================
echo.
echo Este modo digita fornecedores no campo de busca do Ongsys e mostra
echo quais sugestoes aparecem.
echo.
echo Ele NAO clica em nenhuma sugestao e NAO salva nada. Serve para
echo conferir se o programa vai reconhecer os fornecedores da planilha
echo antes de tentar um lancamento de verdade.
echo.
echo Voce pode informar uma planilha (ele testa todos os fornecedores
echo encontrados nela) ou digitar nomes na linha de comando com --nome.
echo.
pause

%PY% automacao_ongsys.py testar-fornecedor
echo.
pause
