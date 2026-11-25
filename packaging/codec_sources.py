"""Collect matching codec sources alongside the desktop ZIP for redistribution."""
import argparse
import hashlib
from importlib.metadata import version
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import tomllib
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["macos-arm64", "windows-x64"])
    parser.add_argument("--from-dir", type=Path, help="Use previously downloaded source files offline")
    args = parser.parse_args()
    manifest = Path(__file__).with_name("codec-sources.toml")
    sources = tomllib.loads(manifest.read_text(encoding="utf-8"))
    for package, expected in sources["versions"].items():
        if version(package) != expected:
            raise SystemExit(f"Update {manifest.name} for the installed {package} version")

    dist = Path("dist")
    dist.mkdir(exist_ok=True)
    output = dist / f"Endoscope-Degrid-{args.target}-codec-sources.zip"
    with TemporaryDirectory(dir=dist) as staging:
        temporary = Path(staging) / output.name
        with ZipFile(temporary, "w") as archive:
            archive.write(manifest, manifest.name)
            archive.write("docs/DISTRIBUTION.md", "DISTRIBUTION.md")
            for item in sources["common"] + sources[args.target]:
                source = (args.from_dir or Path(staging)) / item["file"]
                if args.from_dir is None:
                    subprocess.run(["curl", "--fail", "--location", "--retry", "3",
                                    "--output", str(source), item["url"]], check=True)
                with source.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != item["sha256"]:
                    raise SystemExit(f"SHA-256 mismatch: {source.name}")
                archive.write(source, source.name)
        temporary.replace(output)
    print(output.resolve())


if __name__ == "__main__":
    main()
