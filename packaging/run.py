"""Launcher used inside the AppImage (and by `make run`)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sc_hammer.__main__ import main  # noqa: E402

sys.exit(main())
