"""Public build identity; never includes host configuration or credentials."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def identity():
    def read(name, fallback):
        try:
            return (ROOT / name).read_text().strip() or fallback
        except OSError:
            return fallback
    return {'version': read('VERSION', 'unknown'), 'revision': read('REVISION', 'development')}
