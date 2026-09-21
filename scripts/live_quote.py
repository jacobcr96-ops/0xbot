#!/usr/bin/env python3
"""Shim → ``python -m x_intel.tools.live_quote`` (supports --chain/--ca)."""
from __future__ import annotations

import sys

from x_intel.tools.live_quote import main

if __name__ == "__main__":
    raise SystemExit(main())
