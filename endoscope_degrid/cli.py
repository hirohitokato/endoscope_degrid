import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys
import time

from . import __version__
from .algorithms import Settings, VideoReconstructor
from .media import process_video, scan_video
from .mlx_backend import MetalUnavailable, metal_reconstructor
from .reporting import REFERENCES, diagnostics, write_json


def parser():
    root = argparse.ArgumentParser(description="Fibre-bundle grid removal without learned image generation")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in [("analyze", "Inspect a video and save calibration / still previews"),
                            ("process", "Process one video, optionally a short interval"),
                            ("batch", "Process multiple videos independently"),
                            ("compare", "Compare spectral and core reconstruction on sampled frames")]:
        p = commands.add_parser(name, help=help_text)
        p.add_argument("inputs", nargs="+" if name == "batch" else None, type=Path)
        if name in ("analyze", "compare"):
            p.add_argument("--report-dir", type=Path, required=True)
        else:
            p.add_argument("-o", "--output-dir" if name == "batch" else "--output", dest="output", type=Path, required=True)
            p.add_argument("--report-dir", type=Path)
            p.add_argument("--start", type=float, default=0)
            p.add_argument("--duration", type=float)
            p.add_argument("--crf", type=int, default=18)
            p.add_argument("--backend", choices=("auto", "cpu", "metal"), default="auto",
                           help="Notch processing device (auto uses Metal if available)")
            p.add_argument("--frame-workers", type=int, choices=range(1, 9), default=1,
                           help="Concurrent notch frames; --workers applies to each frame")
            p.add_argument("--preset", choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"], default="medium")
            p.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing output")
        p.add_argument("--method", choices=["notch", "core"], default="notch")
        p.add_argument("--strength", type=float, default=1.0, help="0..1; fraction of the correction to apply")
        p.add_argument("--bandwidth", type=float, default=0.18, help="Notch radius as a fraction of fundamental frequency, 0.05..0.30")
        p.add_argument("--samples", type=int, default=24)
        p.add_argument("--max-peaks", type=int, default=100)
        p.add_argument("--peak-db", type=float, default=12)
        p.add_argument("--min-frequency", type=float, default=0.025, help="Minimum candidate frequency, cycles/full-resolution pixel")
        p.add_argument("--workers", type=int, default=4, help="FFT worker threads")
        p.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"))
        p.add_argument("--quiet", action="store_true")
    return root


def _settings(args):
    return Settings(**{k: getattr(args, k) for k in Settings.__dataclass_fields__})


def _processing_backend(model, requested):
    if requested == "cpu" or model.settings.method != "notch":
        return "cpu", model
    try:
        return "metal", metal_reconstructor(model)
    except MetalUnavailable:
        if requested == "metal":
            raise
        return "cpu", model


def _run_one(source, args, destination=None, report_dir=None):
    settings = _settings(args)
    settings.validate()
    if destination is not None and settings.method != "notch":
        if args.frame_workers != 1 or args.backend == "metal":
            raise ValueError("Metal and multiple frame workers require --method notch")
    source = source.resolve(strict=True)
    if destination is not None:
        if destination.resolve() == source:
            raise ValueError("入力と出力は別ファイルにしてください。")
        if destination.exists() and not args.overwrite:
            raise FileExistsError(f"既存出力は上書きしません: {destination}")
    report_dir = report_dir or args.report_dir
    if report_dir is None:
        report_dir = destination.parent/(destination.stem+"_report")
    report_dir = report_dir.resolve()
    # Never let a report directory hide the source or output file.
    if report_dir == source or (destination is not None and report_dir == destination.resolve()):
        raise ValueError("report-dir must be a separate directory")
    progress = None if args.quiet else lambda text: print(f"[{source.parent.name}/{source.name}] {text}", file=sys.stderr, flush=True)
    started = time.perf_counter()
    scan = scan_video(source, settings.samples, progress)
    report = dict(version=__version__, status="calibrating", source=scan.metadata, references=REFERENCES)
    report_dir.mkdir(parents=True, exist_ok=True)
    try:
        if args.command == "compare":
            report["methods"] = {}
            for method in ("notch", "core"):
                if progress:
                    progress(f"calibrate {method}")
                model = VideoReconstructor(scan.samples, scan.metadata["field_order"] != "progressive", replace(settings, method=method), args.roi)
                report["methods"][method] = dict(calibration=model.describe(), diagnostics=diagnostics(scan, model, report_dir/method))
            report["status"] = "comparison_complete"
        else:
            if progress:
                progress(f"calibrate {settings.method}")
            model = VideoReconstructor(scan.samples, scan.metadata["field_order"] != "progressive", settings, args.roi)
            report["calibration"] = model.describe()
            report["diagnostics"] = diagnostics(scan, model, report_dir)
            report["status"] = "calibrated"
            write_json(report_dir/"report.json", report)
            if destination is not None:
                backend, processing_model = _processing_backend(model, args.backend)
                if progress:
                    progress(f"backend {backend}")
                report["output"] = process_video(scan, processing_model, destination, start=args.start,
                    duration=args.duration, crf=args.crf, preset=args.preset,
                    overwrite=args.overwrite, progress=progress, frame_workers=args.frame_workers)
                report["output"].update(backend=backend, frame_workers=args.frame_workers)
                report["status"] = "complete"
        report["elapsed_seconds"] = time.perf_counter()-started
        write_json(report_dir/"report.json", report)
        return report_dir/"report.json"
    except BaseException as error:
        report["status"] = "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
        report["error"] = str(error) or type(error).__name__
        report["elapsed_seconds"] = time.perf_counter()-started
        write_json(report_dir/"report.json", report)
        raise


def batch_destination(source, directory):
    path = source.resolve()
    key = hashlib.sha256(str(path).encode()).hexdigest()[:8]
    return directory/f"{path.parent.name}_{path.stem}_{key}_degrid{path.suffix.lower()}"


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        _settings(args).validate()
        if args.command == "batch":
            records = []
            failures = 0
            for source in dict.fromkeys(args.inputs):
                destination = batch_destination(source, args.output)
                report_dir = (args.report_dir/destination.stem) if args.report_dir else None
                try:
                    result = _run_one(source, args, destination, report_dir)
                    records.append(dict(source=str(source), status="complete", report=str(result)))
                    print(result)
                except (ValueError, OSError, RuntimeError) as error:
                    failures += 1
                    records.append(dict(source=str(source), status="failed", error=str(error)))
                    print(f"ERROR: {source}: {error}", file=sys.stderr)
            args.output.mkdir(parents=True, exist_ok=True)
            write_json(args.output/"batch_report.json", records)
            return 1 if failures else 0
        result = _run_one(args.inputs, args, getattr(args, "output", None))
        print(result)
        return 0
    except KeyboardInterrupt:
        print("Cancelled; incomplete video was removed.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
