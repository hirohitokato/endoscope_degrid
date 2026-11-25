"""Frequency-selective and irregular core-sampling reconstruction.

No spatial blur, morphological operations, or learned model is used here.
See docs/METHODS.md for sources, modifications, and assumptions.
"""
from dataclasses import asdict, dataclass

import numpy as np
from scipy.fft import fft2, ifft2, irfft2, next_fast_len, rfft2
from scipy.interpolate import CloughTocher2DInterpolator
from scipy.spatial import Delaunay


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    method: str = "notch"
    strength: float = 1.0
    bandwidth: float = 0.18
    samples: int = 24
    max_peaks: int = 100
    peak_db: float = 12.0
    min_frequency: float = 0.025
    workers: int = 4

    def validate(self):
        if self.method not in ("notch", "core"):
            raise ValueError("method must be notch or core")
        if not 0 <= self.strength <= 1:
            raise ValueError("strength must be between 0 and 1")
        if not 0.05 <= self.bandwidth <= 0.3:
            raise ValueError("bandwidth must be between 0.05 and 0.30")
        if not 4 <= self.samples <= 128:
            raise ValueError("samples must be between 4 and 128")
        if not 6 <= self.max_peaks <= 200:
            raise ValueError("max-peaks must be between 6 and 200")
        if not 6 <= self.peak_db <= 40:
            raise ValueError("peak-db must be between 6 and 40")
        if not 0.005 <= self.min_frequency <= 0.2:
            raise ValueError("min-frequency must be between 0.005 and 0.2")
        if not 1 <= self.workers <= 32:
            raise ValueError("workers must be between 1 and 32")


def _gray(image):
    if image.dtype != np.uint8 or image.shape[-1] != 3:
        return image.astype(np.float32).mean(axis=2)
    # Three 8-bit samples sum exactly in float32 without casting the RGB image.
    gray = np.add(image[..., 0], image[..., 1], dtype=np.float32)
    np.add(gray, image[..., 2], out=gray)
    gray /= 3
    return gray


def auto_roi(frames):
    """Bounding box from temporal brightness occupancy; no morphology."""
    h, w = frames[0].shape[:2]
    gray = np.median(np.stack([_gray(f) for f in frames]), axis=0, overwrite_input=True)
    high = float(np.quantile(gray, 0.99))
    if high < 8:
        raise CalibrationError("映像が暗すぎて視野を推定できません。明るい区間を指定してください。")
    occupied = gray > max(5.0, high * 0.06)
    rows = np.flatnonzero(occupied.sum(axis=1) > max(4, 0.015*w))
    cols = np.flatnonzero(occupied.sum(axis=0) > max(4, 0.015*h))
    if len(rows) < 64 or len(cols) < 64:
        raise CalibrationError("十分な撮影領域がありません。--roi を指定してください。")
    x0 = max(0, int(cols[0])-32)//2*2
    y0 = max(0, int(rows[0])-32)//2*2
    x1 = min(w, (int(cols[-1])+34)//2*2)
    y1 = min(h, (int(rows[-1])+34)//2*2)
    return x0, y0, x1-x0, y1-y0


def validate_roi(roi, shape, interlaced):
    x, y, w, h = roi
    if min(x, y) < 0 or min(w, h) < 64 or x+w > shape[1] or y+h > shape[0]:
        raise ValueError("ROI must be within the image and at least 64 x 64 pixels")
    if interlaced and (y % 2 or h % 2):
        raise ValueError("interlaced ROI y and height must be even")


def frequency_grid(shape, row_step):
    h, w = shape
    fy, fx = np.meshgrid(np.fft.fftfreq(h)/row_step, np.fft.fftfreq(w), indexing="ij", sparse=True)
    return fx, fy, np.hypot(fx, fy)


def torus_distance(fx, fy, px, py, row_step):
    dx = abs(fx-px); dx = np.minimum(dx, 1-dx)
    dy = abs(fy-py); dy = np.minimum(dy, 1/row_step-dy)
    return dx*dx + dy*dy


def _candidates(score, fx, fy, row_step, separation, limit=100, threshold=8):
    remaining = score.copy()
    peaks = []
    for _ in range(limit):
        iy, ix = np.unravel_index(remaining.argmax(), remaining.shape)
        value = float(remaining[iy, ix])
        if value < threshold:
            break
        px, py = float(fx[0, ix]), float(fy[iy, 0])
        peaks.append((px, py, value))
        remaining[torus_distance(fx, fy, px, py, row_step) < separation**2] = -1000
    return peaks


def _fundamental(candidates):
    """Require a near-hexagonal first ring, not an isolated texture peak."""
    ordered = sorted(candidates, key=lambda p: -p[2])
    for px, py, value in ordered:
        radius = np.hypot(px, py)
        if value < 16 or radius > 0.24:
            continue
        a = np.arctan2(py, px)
        ring = []
        for k in range(6):
            direction = a + k*np.pi/3
            matched = []
            for qx, qy, qv in candidates:
                qr = np.hypot(qx, qy)
                angular = abs(np.angle(np.exp(1j*(np.arctan2(qy, qx)-direction))))
                if abs(qr/radius-1) < 0.22 and angular < 0.23:
                    matched.append((qv, qr))
            if matched:
                ring.append(max(matched))
        if len(ring) == 6 and min(v for v, _ in ring) >= 12:
            return float(np.median([r for _, r in ring]))
    raise CalibrationError("再現性のある六角格子を検出できません。自動処理を停止しました。"
                           "--roi / --min-frequency を確認してください。")


class SpectralField:
    def __init__(self, samples, row_step, settings):
        self.settings = settings
        self.row_step = row_step
        self.shape = samples[0].shape[:2]
        h, w = self.shape
        self.pad = (max(8, 24//row_step), 24)
        ph, pw = self.pad
        self.fft_shape = (next_fast_len(h+2*ph), next_fast_len(w+2*pw))
        shape = self.fft_shape
        self.fx, self.fy, self.radius = frequency_grid(shape, row_step)
        win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
        power_samples = []
        for sample in samples:
            gray = _gray(sample)
            p = abs(fft2((gray-gray.mean())*win, s=shape, workers=settings.workers))**2
            power_samples.append(p / max(float(p.sum()), 1e-20))
        power = np.median(power_samples, axis=0)
        bins = np.rint(self.radius*max(shape[1], shape[0]*row_step)).astype(int)
        # Sorting once is substantially faster than repeatedly scanning a full FFT.
        order = np.argsort(bins.ravel())
        sorted_bins = bins.ravel()[order]
        boundaries = np.flatnonzero(np.diff(sorted_bins))+1
        groups = np.split(order, boundaries)
        baseline = np.full(bins.max()+1, 1e-30, np.float64)
        for indices in groups:
            baseline[bins.ravel()[indices[0]]] = np.median(power.ravel()[indices])
        score = 10*np.log10(np.maximum(power, 1e-30)/np.maximum(baseline[bins], 1e-30))
        score[self.radius < settings.min_frequency] = -1000
        candidates = _candidates(score, self.fx, self.fy, row_step, 0.009, 140)
        self.fundamental = _fundamental(candidates)
        self.protected_radius = self.fundamental*0.45
        score[self.radius < self.fundamental*0.65] = -1000
        self.peaks = _candidates(score, self.fx, self.fy, row_step,
                                 self.fundamental*0.235, settings.max_peaks, settings.peak_db)
        if len(self.peaks) < 6:
            raise CalibrationError("格子周波数の検出点が不足しています。")
        width = self.fundamental*settings.bandwidth
        mask = np.ones(shape, np.float64)
        for px, py, _ in self.peaks:
            ds = torus_distance(self.fx, self.fy, px, py, row_step)/width**2
            ds **= 3
            mask *= ds / (ds+1)
        mirror = mask[np.ix_((-np.arange(shape[0])) % shape[0],
                             (-np.arange(shape[1])) % shape[1])]
        mask = np.minimum(mask, mirror)
        # Smoothly protect DC and tissue frequencies below the first lattice ring.
        a = np.clip((self.radius-self.protected_radius)/(self.fundamental*0.15), 0, 1)
        taper = a*a*(3-2*a)
        self.mask = (1-(1-mask)*taper).astype(np.float32)
        self.power = power
        # Only a narrow ROI edge blends to the unchanged input, to suppress seams.
        yy, xx = np.mgrid[:h, :w]
        edge = np.minimum.reduce([xx, w-1-xx, yy*row_step, (h-1-yy)*row_step])
        t = np.clip(edge/12, 0, 1)
        self.blend = (t*t*(3-2*t)).astype(np.float32)

    def filter(self, image):
        h, w = self.shape
        ph, pw = self.pad
        bottom = self.fft_shape[0]-h-ph
        right = self.fft_shape[1]-w-pw
        a = np.pad(image.astype(np.float32, copy=False), ((ph, bottom), (pw, right), (0, 0)), mode="reflect")
        a = np.moveaxis(a, 2, 0).copy()
        ft = rfft2(a, workers=self.settings.workers)
        ft *= self.mask[:, :ft.shape[-1]]
        result = irfft2(ft, s=self.fft_shape, workers=self.settings.workers)
        return np.moveaxis(result[:, ph:ph+h, pw:pw+w], 0, 2)

    def reconstruct(self, image):
        raw = image.astype(np.float32)
        return raw+(self.filter(raw)-raw)*self.blend[:, :, None]*self.settings.strength

    def describe(self):
        return dict(shape=list(self.shape), row_step=self.row_step,
                    fundamental_cycles_per_pixel=self.fundamental,
                    estimated_core_spacing_pixels=2/(np.sqrt(3)*self.fundamental),
                    protected_radius=self.protected_radius, peak_count=len(self.peaks),
                    peaks=[list(p) for p in self.peaks],
                    mean_frequency_attenuation=float(np.mean(1-self.mask)))


class CoreField:
    """Core measurement + C1 piecewise-cubic scattered-data reconstruction."""
    def __init__(self, samples, spectral):
        self.spectral = spectral
        self.settings = spectral.settings
        self.row_step = spectral.row_step
        self.shape = spectral.shape
        residuals = []
        for sample in samples:
            logarithm = np.log(sample.astype(np.float32)/255+0.025)
            residuals.append(logarithm-spectral.filter(logarithm))
        loggain = np.median(residuals, axis=0)
        loggain -= spectral.filter(loggain)
        self.gain = np.exp(np.clip(loggain, -1.8, 1.8))
        spacing = 2/(np.sqrt(3)*spectral.fundamental)
        gray = self.gain.mean(axis=2)
        h, w = self.shape
        allowed = np.ones((h, w), bool)
        allowed[:8] = False; allowed[-8:] = False
        allowed[:, :8] = False; allowed[:, -8:] = False
        rr = spacing*0.68
        r = int(np.ceil(rr))
        yy, xx = np.mgrid[-r:r+1, -r:r+1]
        exclusion = xx**2+(yy*self.row_step)**2 < rr**2
        points = []
        for index in np.argsort(gray.ravel())[::-1]:
            y, x = divmod(int(index), w)
            if gray[y, x] < 1.025:
                break
            if not allowed[y, x]:
                continue
            points.append((x, self.row_step*y))
            y0, y1 = max(0, y-r), min(h, y+r+1)
            x0, x1 = max(0, x-r), min(w, x+r+1)
            allowed[y0:y1, x0:x1] &= ~exclusion[y0-y+r:y1-y+r, x0-x+r:x1-x+r]
        if len(points) < 20:
            raise CalibrationError("ファイバー標本の位置を安定して推定できません。notch を使用してください。")
        self.points = np.asarray(points, dtype=np.float64)
        self.triangulation = Delaunay(self.points)
        yy, xx = np.mgrid[:h, :w]
        self.query = np.column_stack([xx.ravel(), self.row_step*yy.ravel()])
        self.cx = self.points[:, 0].astype(int)
        self.cy = (self.points[:, 1]/self.row_step).astype(int)
        sample_radius = min(4.0, spacing*0.23)
        self.offsets = [(dy, dx) for dy in range(-4, 5) for dx in range(-4, 5)
                        if dx*dx+(dy*self.row_step)**2 <= sample_radius**2]

    def reconstruct(self, image):
        raw = image.astype(np.float32)
        values = np.zeros((len(self.points), 3), np.float64)
        weights = np.zeros_like(values)
        for dy, dx in self.offsets:
            y, x = self.cy+dy, self.cx+dx
            g = self.gain[y, x]
            values += g*raw[y, x]
            weights += g*g
        values /= np.maximum(weights, 1e-12)
        interpolator = CloughTocher2DInterpolator(self.triangulation, values)
        result = interpolator(self.query).reshape(raw.shape)
        valid = np.isfinite(result).all(axis=2)
        # Outside the convex hull, retain the observation rather than extrapolate.
        result[~valid] = raw[~valid]
        return raw+(result-raw)*self.spectral.blend[:, :, None]*self.settings.strength

    def describe(self):
        return {**self.spectral.describe(), "core_count": len(self.points),
                "interpolation": "Clough-Tocher C1 on Delaunay triangulation",
                "gain_estimation": "temporal median of log spectral residuals"}


class VideoReconstructor:
    def __init__(self, samples, interlaced, settings=None, roi=None):
        self.settings = settings or Settings()
        self.settings.validate()
        self.image_shape = samples[0].shape
        if any(im.shape != self.image_shape for im in samples):
            raise ValueError("calibration frame dimensions differ")
        self.interlaced = interlaced
        self.roi = tuple(roi) if roi is not None else auto_roi(samples)
        validate_roi(self.roi, self.image_shape, interlaced)
        x, y, w, h = self.roi
        self.fields = []
        self.spectral_fields = []
        step = 2 if interlaced else 1
        for parity in range(step):
            fields = [s[y+parity:y+h:step, x:x+w] for s in samples]
            spectral = SpectralField(fields, step, self.settings)
            self.spectral_fields.append(spectral)
            self.fields.append(spectral if self.settings.method == "notch" else CoreField(fields, spectral))

    def process(self, image):
        if image.shape != self.image_shape:
            raise ValueError("途中で解像度が変化したため処理を停止しました。")
        if self.settings.strength == 0:
            return image.copy()
        result = image.copy()
        x, y, w, h = self.roi
        step = len(self.fields)
        for parity, field in enumerate(self.fields):
            region = image[y+parity:y+h:step, x:x+w]
            restored = field.reconstruct(region)
            np.clip(restored, 0, 255, out=restored)
            np.rint(restored, out=restored)
            result[y+parity:y+h:step, x:x+w] = restored
        return result

    def describe(self):
        return dict(settings=asdict(self.settings), roi=list(self.roi),
                    interlaced_fields_processed_independently=self.interlaced,
                    fields=[f.describe() for f in self.fields])
