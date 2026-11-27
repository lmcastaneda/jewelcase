"""What the packaged app runs: the window by default, or any `jewelcase` command given as arguments."""
import sys

from jewelcase.cli import main

if __name__ == "__main__":
    sys.exit(main())
