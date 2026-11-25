"""Run the shipped executable outside the checkout against an audio/video fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from test_algorithms import synthetic_frames
from test_media import fixture_video


@pytest.mark.skipif("DEGRID_ARCHIVE" not in os.environ, reason="requires a built desktop ZIP")
def test_archive_processes_interlaced_video(tmp_path):
    archive = Path(os.environ["DEGRID_ARCHIVE"]).resolve(strict=True)
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", archive, tmp_path], check=True)
        executable = tmp_path / "Endoscope Degrid.app/Contents/MacOS/Endoscope Degrid"
    else:
        with zipfile.ZipFile(archive) as package:
            package.extractall(tmp_path)
        executable = tmp_path / "Endoscope Degrid/endoscope-degrid-cli.exe"
        assert (executable.parent / "Endoscope Degrid.exe").is_file()

    source = tmp_path / "検証 動画.mp4"
    _, frames = synthetic_frames(n=18, interlaced=True)
    fixture_video(source, variable=True, frames=frames)
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    env["PATH"] = os.defpath
    def run(*args):
        subprocess.run([executable, *args], cwd=tmp_path, env=env, check=True, timeout=150)

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
