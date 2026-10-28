"""Automação local e conferível de contas a pagar no Ongsys.

O pacote é dividido em camadas para que a parte que apenas lê e confere
(planejamento, relatórios, auditoria de dados) possa ser testada sem
navegador e sem tocar no Ongsys.
"""

__all__ = ["VERSAO"]

VERSAO = "2.0.0"
