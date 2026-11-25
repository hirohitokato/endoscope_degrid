#!/usr/bin/env python3
"""Independent source/output timing, audio, and held-out image inspection."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import av
import cv2
import numpy as np
from scipy.fft import fft2

from endoscope_degrid.media import field_order, rgb_frame
from endoscope_degrid.reporting import label, write_json, write_rgb


def stream_signature(path):
    times = []
    audio = {}
    orders = set()
    with av.open(str(path), options={"err_detect": "explode"}) as c:
        video = c.streams.video[0]
        dimensions = [video.width, video.height]
        for a in c.streams.audio:
            audio[a.index] = [hashlib.sha256(), hashlib.sha256(), 0]
        for packet in c.demux():
            if packet.stream.type == "video":
                for f in packet.decode():
                    if f.pts is None or f.is_corrupt:
                        raise ValueError("invalid output frame")
                    times.append(Fraction(f.pts)*f.time_base)
                    orders.add(field_order(f))
            elif packet.stream.type == "audio" and packet.dts is not None:
                payload, timing, _ = audio[packet.stream.index]
                payload.update(bytes(packet))
                for t in (packet.pts, packet.dts, packet.duration):
                    timing.update((str(None if t is None else Fraction(t)*packet.time_base)+"|").encode())
                audio[packet.stream.index][2] += 1
        duration = Fraction(video.duration)*video.time_base
    return dict(times=times, dimensions=dimensions, orders=orders, duration=duration,
                audio=[dict(payload_sha256=p.hexdigest(), timing_sha256=t.hexdigest(), packets=n)
                       for p, t, n in audio.values()])


def at_time(path, t):
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        c.seek(int(t/v.time_base), stream=v, backward=True)
        for f in c.decode(v):
            if f.time >= t-1e-7:
                return rgb_frame(f), float(f.time)
    raise ValueError("time outside video")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--report-dir", type=Path, required=True)
    args = p.parse_args()
    report = json.loads((args.report_dir/"report.json").read_text())
    a = stream_signature(args.source); b = stream_signature(args.output)
    origin = Fraction(str(report["output"]["start_seconds"]))
    assert report["output"]["start_seconds"] == 0, "full-video validation only"
    checks = dict(frame_times_exact=a["times"] == b["times"],
                  dimensions_exact=a["dimensions"] == b["dimensions"],
                  field_orders_exact=a["orders"] == b["orders"],
                  duration_exact=a["duration"] == b["duration"],
                  audio_payload_and_timing_exact=a["audio"] == b["audio"])
    if not all(checks.values()):
        raise ValueError(str(checks))
    x, y, w, h = report["calibration"]["roi"]
    masks = np.load(args.report_dir/"calibration.npz")
    observations = []
    for j, t in enumerate([10.0, 51.0, 101.0, 151.0, 231.0, 302.0]):
        if t >= float(a["duration"]):
            continue
        raw, actual = at_time(args.source, t)
        processed, actual_out = at_time(args.output, actual)
        assert abs(actual-actual_out) < 1e-6
        energy_a = energy_b = 0.0
        fields = len(report["calibration"]["fields"])
        for parity in range(fields):
            mask = masks[f"mask_{parity}"]
            ia = raw[y+parity:y+h:fields, x:x+w].mean(axis=2)
            ib = processed[y+parity:y+h:fields, x:x+w].mean(axis=2)
            window = np.outer(np.hanning(ia.shape[0]), np.hanning(ia.shape[1]))
            fa = fft2((ia-ia.mean())*window, s=mask.shape)
            fb = fft2((ib-ib.mean())*window, s=mask.shape)
            energy_a += float(np.sum(abs(fa[mask < .2])**2))
            energy_b += float(np.sum(abs(fb[mask < .2])**2))
        observations.append(dict(time_seconds=actual,
            grid_band_energy_reduction_db=float(10*np.log10(max(energy_a, 1e-20)/max(energy_b, 1e-20)))))
        crop = (slice(y+h//2-190, y+h//2+190), slice(x+w//2-190, x+w//2+190))
        write_rgb(args.report_dir/f"heldout_detail_{j}.png", np.concatenate([
            label(raw[crop], f"Original {actual:.2f}s"), label(processed[crop], "Encoded output")], axis=1))
        write_rgb(args.report_dir/f"heldout_full_{j}.png", np.concatenate([
            label(cv2.resize(raw, (640, 360)), "Original"),
            label(cv2.resize(processed, (640, 360)), "Encoded output")], axis=1))
    result = dict(checks=checks, frames=len(b["times"]), duration_seconds=float(b["duration"]),
                  field_orders=sorted(b["orders"]), audio=b["audio"], heldout_observations=observations,
                  caveat="Held-out frame suppression, not a ground-truth fidelity or resolution measurement.")
    write_json(args.report_dir/"independent_validation.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
