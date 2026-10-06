"""python_deps package."""
import sys
from pathlib import Path

_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)
