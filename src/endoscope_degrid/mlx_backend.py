"""Apple Silicon GPU processing for a calibrated notch filter."""
from functools import cache
from importlib.util import find_spec
import platform
import subprocess
import sys
from threading import local

import numpy as np


class MetalUnavailable(RuntimeError):
    pass


def probe_metal():
    import mlx.core as mx
    return 0 if mx.metal.is_available() else 1


@cache
def _has_metal():
    # A headless MLX import can abort the interpreter on some installations.
    try:
        arguments = ["--probe-metal"] if getattr(sys, "frozen", False) else [
            "-c", "import mlx.core as mx; raise SystemExit(not mx.metal.is_available())",
        ]
        probe = subprocess.run(
            [sys.executable, *arguments],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def metal_reconstructor(model):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise MetalUnavailable("Metal processing requires Apple Silicon")
    if find_spec("mlx") is None:
        raise MetalUnavailable("Install the optional MLX dependency to use Metal")
    if not _has_metal():
        raise MetalUnavailable("Metal GPU is unavailable")
    import mlx.core as mx

    return MetalNotch(model, mx)


class MetalNotch:
    def __init__(self, model, mx):
        self.model = model
        self.mx = mx
        self.stream = mx.new_thread_local_stream(mx.gpu)
        self.worker = local()
        fields = model.spectral_fields
        h, w = fields[0].shape
        ph, pw = fields[0].pad
        fft_shape = fields[0].fft_shape
        step = len(fields)
        rows = np.pad(np.arange(h, dtype=np.int32), (ph, fft_shape[0]-h-ph), mode="reflect")
        cols = np.pad(np.arange(w, dtype=np.int32), (pw, fft_shape[1]-w-pw), mode="reflect")
        field_rows = rows[None, :, None]*step + np.arange(step, dtype=np.int32)[:, None, None]
        reflected = mx.array(field_rows*w + cols[None, None, :])
        masks = mx.array(np.stack([f.mask[:, :fft_shape[1]//2+1] for f in fields])[:, None])
        blend = mx.array(np.stack([f.blend for f in fields])[:, None])
        strength = model.settings.strength

        def restore(region):
            # Keep interlaced fields separate along the leading batch axis.
            raw = region.reshape(h, step, w, 3).transpose(1, 3, 0, 2)
            # Gather the entire reflected RGB border in one GPU pass.
            padded = mx.take(region.reshape(h*step*w, 3), reflected, axis=0)
            padded = padded.transpose(0, 3, 1, 2).astype(mx.float32)
            transformed = mx.fft.rfft2(padded)
            restored = mx.fft.irfft2(transformed*masks, s=fft_shape)[:, :, ph:ph+h, pw:pw+w]
            raw = raw.astype(mx.float32)
            mixed = raw + (restored - raw)*blend*strength
            quantized = mx.round(mx.clip(mixed, 0, 255)).astype(mx.uint8)
            return quantized.transpose(2, 0, 3, 1).reshape(h*step, w, 3)

        # Worker graphs share only already evaluated, read-only arrays.
        mx.eval(reflected, masks, blend)
        self.restore = restore

    def process(self, image):
        return self.process_inplace(image.copy())

    def process_inplace(self, image):
        """Apply the notch filter to an RGB buffer owned by the caller."""
        if image.shape != self.model.image_shape:
            raise ValueError("途中で解像度が変化したため処理を停止しました。")
        if self.model.settings.strength == 0:
            return image
        x, y, w, h = self.model.roi
        region = np.ascontiguousarray(image[y:y+h, x:x+w])
        with self.mx.stream(self.stream):
            if not hasattr(self.worker, "restore"):
                self.worker.restore = self.mx.compile(self.restore)
            restored = self.worker.restore(self.mx.array(region))
            self.mx.eval(restored)
        image[y:y+h, x:x+w] = np.array(restored, copy=False)
        return image
