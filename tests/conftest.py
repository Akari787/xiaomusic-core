"""Make the repository root importable so tests can share helpers
(``tests.player_source``) regardless of how pytest is started."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
