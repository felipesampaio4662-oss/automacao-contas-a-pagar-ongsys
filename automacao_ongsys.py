from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ongsys.cli import executar_cli

if __name__ == "__main__":
    sys.exit(executar_cli())
