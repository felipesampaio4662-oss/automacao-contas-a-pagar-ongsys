from __future__ import annotations

import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from ongsys.painel import iniciar_painel


if __name__ == "__main__":
    iniciar_painel()
