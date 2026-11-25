import json
from pathlib import Path

import cv2
import numpy as np
from scipy.fft import fft2


REFERENCES = [
    {"role": "spectral notch principle; modified implementation",
     "doi": "10.1117/1.JMI.11.3.034002", "year": 2024,
     "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11099938/"},
    {"role": "core sampling and triangulation; modified calibration and interpolation",
     "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5854078/", "year": 2018},
    {"role": "alternative investigated, not a production method",
     "doi": "10.1063/1.5143929", "year": 2020},
]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    temporary.replace(path)


def write_rgb(path, image):
    if not cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR)):
        raise OSError(f"画像を書き込めません: {path}")


def label(image, title):
    result = cv2.copyMakeBorder(image, 32, 0, 0, 0, cv2.BORDER_CONSTANT)
    cv2.putText(result, title, (12, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 255, 255), 1, cv2.LINE_AA)
    return result


def diagnostics(scan, model, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    arrays = {}
    for i, field in enumerate(model.spectral_fields):
        arrays[f"mask_{i}"] = field.mask
        arrays[f"power_{i}"] = field.power
        logpower = np.log1p(np.fft.fftshift(field.power)*1e12)
        shown = np.rint(logpower/max(float(logpower.max()), 1e-20)*255).astype(np.uint8)
        colored = cv2.applyColorMap(shown, cv2.COLORMAP_INFERNO)
        mask = cv2.cvtColor(np.rint(np.fft.fftshift(field.mask)*255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        pair = np.concatenate([cv2.resize(colored, (480, 480)), cv2.resize(mask, (480, 480))], axis=1)
        cv2.imwrite(str(directory/f"spectrum_mask_field_{i}.png"), pair)
    np.savez_compressed(directory/"calibration.npz", **arrays)
    metrics = []
    x, y, w, h = model.roi
    for j, k in enumerate(sorted(set([1, len(scan.samples)//2, len(scan.samples)-2]))):
        original = scan.samples[k]
        restored = model.process(original)
        crop = (slice(y+h//2-190, y+h//2+190), slice(x+w//2-190, x+w//2+190))
        if min(w, h) >= 380:
            pair = np.concatenate([label(original[crop], "Original (native pixels)"),
                                   label(restored[crop], f"Processed: {model.settings.method}")], axis=1)
            write_rgb(directory/f"detail_{j}.png", pair)
        pair = np.concatenate([label(cv2.resize(original, (640, round(original.shape[0]*640/original.shape[1]))), "Original"),
                               label(cv2.resize(restored, (640, round(restored.shape[0]*640/restored.shape[1]))), "Processed")], axis=1)
        write_rgb(directory/f"comparison_{j}.png", pair)
        before_energy = after_energy = 0.0
        for parity, field in enumerate(model.spectral_fields):
            step = len(model.fields)
            a = original[y+parity:y+h:step, x:x+w].astype(np.float32).mean(axis=2)
            b = restored[y+parity:y+h:step, x:x+w].astype(np.float32).mean(axis=2)
            win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
            fa = fft2((a-a.mean())*win, s=field.fft_shape)
            fb = fft2((b-b.mean())*win, s=field.fft_shape)
            band = field.mask < 0.2
            before_energy += float(np.sum(abs(fa[band])**2))
            after_energy += float(np.sum(abs(fb[band])**2))
        reduction = 10*np.log10(max(before_energy, 1e-20)/max(after_energy, 1e-20))
        metrics.append(dict(time_seconds=float(scan.sample_times[k]-scan.times[0]),
                            grid_band_energy_reduction_db=float(reduction)))
    return dict(directory=str(directory), calibration_sample_metrics=metrics,
                interpretation="Grid-band suppression is not a measure of anatomical fidelity or recovered resolution. "
                               "These frames were also used for calibration; this is an internal diagnostic, not a held-out benchmark.")

