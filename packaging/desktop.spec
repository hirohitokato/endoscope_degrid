from importlib.metadata import distribution, version
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

mac = sys.platform == "darwin"
root = Path(SPECPATH).parent
data = [(str(root / "docs" / "DISTRIBUTION.md"), ".")]
binaries = []
packages = ["av", "numpy", "scipy", "opencv-python-headless", "pyinstaller"]
if mac:
    packages.extend(["mlx", "mlx-metal"])
    binaries = collect_dynamic_libs("mlx")
    data += collect_data_files("mlx", includes=["lib/*.metallib"])

for name in packages:
    dist = distribution(name)
    for file in dist.files or []:
        if ".dist-info/" in str(file) and file.name.lower().startswith(("license", "copying", "notice")):
            data.append((str(dist.locate_file(file)), "licenses/" + str(file.parent)))
python_license = Path(sys.base_prefix) / "LICENSE.txt" if sys.platform == "win32" else (
    Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
)
if python_license.exists():
    data.append((str(python_license), "licenses/python"))

entries = [str(root / "packaging" / "launcher.py")]
if not mac:
    entries.append(str(root / "degrid.py"))
a = Analysis(entries, pathex=[str(root / "src")],
             # mlx.core imports both Python helpers from its native extension.
             hiddenimports=["mlx._reprlib_fix", "mlx.__array_api_info"] if mac else [],
             binaries=binaries, datas=data, excludes=["pytest"])
pyz = PYZ(a.pure)
# Both executables need the runtime hooks, each with its own entry script.
gui_scripts = [entry for entry in a.scripts if entry[0] != "degrid"]
gui = EXE(pyz, gui_scripts, [("X utf8", None, "OPTION")], exclude_binaries=True, name="Endoscope Degrid",
          console=False, upx=False)
launchers = [gui]
if not mac:
    console_scripts = [entry for entry in a.scripts if entry[0] != "launcher"]
    launchers.append(EXE(pyz, console_scripts, [("X utf8", None, "OPTION")], exclude_binaries=True, name="endoscope-degrid-cli",
                         console=True, upx=False))
folder = COLLECT(*launchers, a.binaries, a.datas, name="Endoscope Degrid", upx=False)
if mac:
    app = BUNDLE(folder, name="Endoscope Degrid.app", version=version("endoscope-degrid"),
                 bundle_identifier="io.github.hirohitokato.endoscope-degrid")
