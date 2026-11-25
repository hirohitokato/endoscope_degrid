"""Build a native desktop ZIP. Run from the repository root using uv."""
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tkinter

from PyInstaller import __main__ as pyinstaller


def main():
    target = (sys.platform, platform.machine().lower())
    labels = {("win32", "amd64"): "windows-x64", ("darwin", "arm64"): "macos-arm64"}
    if target not in labels:
        raise SystemExit(f"Unsupported build platform: {target}")
    tkinter.Tcl()  # Fail during build if the build interpreter lacks Tcl.
    pyinstaller.run(["--noconfirm", "packaging/desktop.spec"])
    dist = Path("dist")
    archive = dist / ("Endoscope-Degrid-" + labels[target] + ".zip")
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-c", "-k", "--keepParent", dist / "Endoscope Degrid.app",
                        archive], check=True)
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip", dist, "Endoscope Degrid")
    print(archive.resolve())


if __name__ == "__main__":
    main()
