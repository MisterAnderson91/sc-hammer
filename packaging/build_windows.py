"""Builds dist/SC-Hammer-<version>-windows-x64.exe, a single self-contained executable (Python, Tk
and the app inside). Run it on Windows with Python 3.9+:

    pip install pyinstaller
    python packaging/build_windows.py

VERSION in the environment overrides the version in sc_hammer/__init__.py, like the AppImage build.
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build", "windows")
DIST = os.path.join(ROOT, "dist")


def main():
    init = os.path.join(ROOT, "sc_hammer", "__init__.py")
    version = os.environ.get("VERSION") or re.search(
        r'__version__ = "([^"]+)"', open(init).read()).group(1)

    # Stamp the version into a copy, so the source tree is left alone.
    src = os.path.join(BUILD, "src")
    shutil.rmtree(src, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "sc_hammer"), os.path.join(src, "sc_hammer"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    copy = os.path.join(src, "sc_hammer", "__init__.py")
    text = open(copy).read()
    open(copy, "w").write(re.sub(r'__version__ = "[^"]*"', '__version__ = "%s"' % version, text))

    import PyInstaller.__main__
    name = "SC-Hammer-%s-windows-x64" % version
    PyInstaller.__main__.run([
        os.path.join(ROOT, "packaging", "run.py"),
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", name,
        "--icon", os.path.join(src, "sc_hammer", "assets", "icon.ico"),
        "--paths", src,
        "--add-data", "%s%ssc_hammer/assets" % (os.path.join(src, "sc_hammer", "assets"), os.pathsep),
        "--hidden-import", "sc_hammer.winhid",
        "--hidden-import", "sc_hammer.gui.app",
        "--distpath", DIST,
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
    ])
    print("Built %s" % os.path.join(DIST, name + ".exe"))


if __name__ == "__main__":
    sys.exit(main())
