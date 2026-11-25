from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from endoscope_degrid.media import _field_order_reader, _setfield_graph, process_video, scan_video
from endoscope_degrid.cli import batch_destination, main


class Identity:
    def process(self, frame):
        return frame.copy()


def fixture_video(path, order="tff", variable=False, audio_offset=0, frames=None):
    with av.open(str(path), "w", options={"movie_timescale": "240000"}) as out:
        audio = out.add_stream("aac", rate=48000)
        audio.layout = "stereo"
        video = out.add_stream("libx264", rate=Fraction(30000, 1001))
        video.width, video.height = (192, 128) if frames is None else frames[0].shape[1::-1]
        video.pix_fmt = "yuv420p"
        video.time_base = Fraction(1, 60000)
        video.codec_context.time_base = Fraction(1, 60000)
        video.codec_context.sample_aspect_ratio = Fraction(4, 3)
        video.codec_context.colorspace = 1
        video.codec_context.color_range = 1
        video.options = {"crf": "12", "preset": "ultrafast"}
        if order != "progressive":
            video.options["x264-params"] = f"interlaced=1:{order}=1"
            video.codec_context.flags |= av.codec.context.Flags.interlaced_dct | av.codec.context.Flags.interlaced_me
        graph = None
        for i in range(18):
            if frames is None:
                image = np.full((128, 192, 3), 80+i*3, np.uint8)
                image[1::2, :, 1] = 170
            else:
                image = frames[i]
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts = i*2002+(500 if variable and i > 8 else 0)
            frame.time_base = Fraction(1, 60000)
            if graph is None:
                graph = _setfield_graph(frame, order)
            graph.push(frame)
            for p in video.encode(graph.pull()):
                out.mux(p)
        for p in video.encode():
            out.mux(p)
        for start in range(0, 28672, 1024):
            t = np.arange(start, start+1024)/48000
            signal = (0.1*np.sin(2*np.pi*440*t)).astype(np.float32)
            frame = av.AudioFrame.from_ndarray(np.stack([signal, signal]), format="fltp", layout="stereo")
            frame.sample_rate = 48000; frame.pts = start + audio_offset; frame.time_base = Fraction(1, 48000)
            for p in audio.encode(frame):
                out.mux(p)
        for p in audio.encode():
            out.mux(p)


def test_field_order_reader_tracks_changes_on_the_same_frame_layout():
    read_order = _field_order_reader()
    for index, order in enumerate(("tff", "bff", "progressive", "tff")):
        frame = av.VideoFrame(192, 128, "yuv420p")
        frame.pts = index
        frame.time_base = Fraction(1, 30)
        graph = _setfield_graph(frame, order)
        graph.push(frame)
        assert read_order(graph.pull()) == order


@pytest.mark.parametrize("frame_workers", [1, 2])
@pytest.mark.parametrize("order", ["tff", "bff", "progressive"])
def test_preserves_real_timestamps_audio_and_field_order(tmp_path, order, frame_workers):
    source = tmp_path/"source.mp4"
    fixture_video(source, order)
    scan = scan_video(source, 6)
    assert scan.metadata["field_order"] == order
    assert scan.metadata["decoded_frame_count"] == 18
    result = process_video(scan, Identity(), tmp_path/"result.mp4", preset="ultrafast", frame_workers=frame_workers)
    assert result["verification"]["presentation_timestamps_exact"]
    assert result["verification"]["audio_packet_content_and_timing_exact"]
    with av.open(str(tmp_path/"result.mp4")) as output:
        assert output.streams.video[0].sample_aspect_ratio == Fraction(4, 3)
        assert output.streams.video[0].codec_context.colorspace == 1


def test_preserves_sub_millisecond_audio_start(tmp_path):
    source = tmp_path/"source.mp4"
    fixture_video(source, audio_offset=1235)
    scan = scan_video(source, 6)
    result = process_video(scan, Identity(), tmp_path/"result.mp4", preset="ultrafast")
    assert result["verification"]["audio_packet_content_and_timing_exact"]


def test_variable_timestamps_and_preview(tmp_path):
    source = tmp_path/"source.mp4"
    fixture_video(source, "progressive", variable=True)
    scan = scan_video(source, 6)
    assert scan.metadata["variable_frame_rate"]
    result = process_video(scan, Identity(), tmp_path/"preview.mp4", start=.13, duration=.26, preset="ultrafast")
    assert result["verification"]["presentation_timestamps_exact"]
    assert 0 < result["frames"] < 18


def test_no_overwrite_and_cancellation_removes_partial_video(tmp_path):
    source = tmp_path/"source.mp4"
    fixture_video(source)
    scan = scan_video(source, 6)
    with pytest.raises(ValueError, match="別"):
        process_video(scan, Identity(), source)
    destination = tmp_path/"protected.mp4"
    destination.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        process_video(scan, Identity(), destination)
    assert destination.read_bytes() == b"keep"
    class Cancel:
        def process(self, frame):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        process_video(scan, Cancel(), tmp_path/"cancel.mp4", frame_workers=2)
    assert not (tmp_path/"cancel.mp4").exists()
    assert not list(tmp_path.glob("*.partial.mp4"))
    assert not list(tmp_path.glob(".*.partial.mp4"))


def test_checksum_failure_preserves_existing_output(tmp_path, monkeypatch):
    source = tmp_path/"source.mp4"
    fixture_video(source)
    scan = scan_video(source, 6)
    destination = tmp_path/"protected.mp4"
    destination.write_bytes(b"keep")

    def fail_checksum(_path):
        raise OSError("checksum read failed")

    monkeypatch.setattr("endoscope_degrid.media.digest", fail_checksum)
    with pytest.raises(OSError, match="checksum read failed"):
        process_video(scan, Identity(), destination, overwrite=True, frame_workers=2)
    assert destination.read_bytes() == b"keep"
    assert not list(tmp_path.glob(".*.partial.mp4"))


def test_batch_destinations_do_not_collide_for_source_names(tmp_path):
    a = batch_destination(Path("a/source.mp4"), tmp_path)
    b = batch_destination(Path("b/source.mp4"), tmp_path)
    assert a != b
    assert main(["batch", str(tmp_path/"missing.mp4"), "-o", str(tmp_path/"output"), "--quiet"]) == 1


def test_desktop_export_preserves_existing_report(tmp_path):
    from endoscope_degrid.cli import parser, run_one

    source = tmp_path / "input.mp4"
    source.touch()
    destination = tmp_path / "output.mp4"
    report_dir = tmp_path / "output_report"
    report_dir.mkdir()
    sentinel = report_dir / "report.json"
    sentinel.write_text("previous export")
    args = parser().parse_args(["process", str(source), "-o", str(destination)])
    with pytest.raises(FileExistsError, match="別の保存名"):
        run_one(source, args, destination, new_report=True)
    assert sentinel.read_text() == "previous export"
    assert not destination.exists()
