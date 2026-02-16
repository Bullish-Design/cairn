"""Allow grail to be run as python -m grail."""

import sys
from grail.cli import main

if __name__ == "__main__":
    sys.exit(main())
