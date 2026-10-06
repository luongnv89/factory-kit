"""``python3 -m factory_kit.run`` — the live driver CLI."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
