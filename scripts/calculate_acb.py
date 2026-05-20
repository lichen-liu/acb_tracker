#!/usr/bin/env python3
"""Standalone lot calculator entry script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acb_tracker.calculate_acb import main


if __name__ == "__main__":
    main()
