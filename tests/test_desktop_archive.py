"""Run the shipped executable outside the checkout against an audio/video fixture."""
import json
import os
import plistlib
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from test_algorithms import synthetic_frames
from test_media import fixture_video


def check_windows_gui(executable, cwd, env):
    with subprocess.Popen([executable], cwd=cwd, env=env) as gui:
        try:
            deadline = time.monotonic() + 60
            while gui.poll() is None and time.monotonic() < deadline:
                title = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                        f"(Get-Process -Id {gui.pid}).MainWindowTitle"],
                                       capture_output=True, timeout=10)
                if title.stdout.strip() == b"Endoscope Degrid":
                    return
                time.sleep(0.25)
            pytest.fail(f"Desktop window did not appear (exit code {gui.poll()})")
        finally:
            if gui.poll() is None:
                gui.terminate()


@pytest.mark.skipif("DEGRID_ARCHIVE" not in os.environ, reason="requires a built desktop ZIP")
def test_archive_processes_interlaced_video(tmp_path):
    archive = Path(os.environ["DEGRID_ARCHIVE"]).resolve(strict=True)
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", archive, tmp_path], check=True)
        contents = tmp_path / "Endoscope Degrid.app/Contents"
        executable = contents / "MacOS/Endoscope Degrid"
        from endoscope_degrid import __version__
        assert plistlib.loads((contents / "Info.plist").read_bytes())["CFBundleShortVersionString"] == __version__
    else:
        with zipfile.ZipFile(archive) as package:
            package.extractall(tmp_path)
        executable = tmp_path / "Endoscope Degrid/endoscope-degrid-cli.exe"
        gui = executable.parent / "Endoscope Degrid.exe"

    source = tmp_path / "検証 動画.mp4"
    _, frames = synthetic_frames(n=18, interlaced=True)
    fixture_video(source, variable=True, frames=frames)
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    env["PATH"] = os.defpath

    def run(*args):
        subprocess.run([executable, *args], cwd=tmp_path, env=env, check=True, timeout=150)

    if sys.platform == "win32":
        usage = subprocess.run([executable], env=env, cwd=tmp_path, capture_output=True, timeout=30)
        assert usage.returncode == 2 and b"usage:" in usage.stderr
        check_windows_gui(gui, tmp_path, env)
    else:
        subprocess.run(["swift", Path(__file__).with_name("check_window.swift"), executable],
                       cwd=tmp_path, check=True, timeout=120)
    run("--version")
    backend = os.getenv("DEGRID_BACKEND", "auto")
    if sys.platform == "darwin" and backend == "auto":
        # Headless runners may abort while probing MLX. On a Metal-capable
        # host, assert that --backend auto also selects it in the frozen app.
        probe = subprocess.run([sys.executable, "-c",
                                "from endoscope_degrid.mlx_backend import probe_metal; raise SystemExit(probe_metal())"],
                               timeout=30, cwd=tmp_path)
        expected_backend = "metal" if probe.returncode == 0 else "cpu"
    else:
        expected_backend = "cpu" if backend == "auto" else backend
    output = tmp_path / "補正 結果.mp4"
    run("process", source, "-o", output, "--samples", "8", "--backend", backend)
    report_dir = tmp_path / "補正 結果_report"
    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert output.stat().st_size > 0
    assert (report_dir / "comparison_0.png").stat().st_size > 0
    assert (report_dir / "spectrum_mask_field_0.png").stat().st_size > 0
    assert report["status"] == "complete"
    assert report["output"]["backend"] == expected_backend
    result = report["output"]["verification"]
    assert result["decoded_frame_count"] == 18
    assert result["field_order"] == "tff"
    assert result["presentation_timestamps_exact"]
    assert result["audio_streams"] and result["audio_packet_content_and_timing_exact"]
