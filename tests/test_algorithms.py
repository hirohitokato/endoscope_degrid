from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from endoscope_degrid.algorithms import CalibrationError, Settings, VideoReconstructor, _gray
from endoscope_degrid.cli import _processing_backend
from endoscope_degrid.mlx_backend import MetalUnavailable, metal_reconstructor


def synthetic_frames(size=256, n=8, interlaced=False):
    yy, xx = np.mgrid[:size, :size]
    f = 0.105
    waves = sum(np.cos(2*np.pi*f*(xx*np.cos(a)+yy*np.sin(a)))
                for a in (0.24, 0.24+np.pi/3, 0.24+2*np.pi/3))
    gain = 1+0.13*waves
    clean, observed = [], []
    for k in range(n):
        value = 100+25*np.sin((xx+k*13)/48)+15*np.cos((yy-k*7)/38)
        if interlaced:
            value[1::2] += 35
        rgb = np.stack([value*.9, value, value*.8], axis=2)
        clean.append(rgb)
        observed.append(np.rint(rgb*gain[:, :, None]).astype(np.uint8))
    return clean, observed


def test_gray_matches_float32_mean_on_strided_rgb_fields():
    image = np.random.default_rng(0).integers(0, 256, size=(64, 96, 3), dtype=np.uint8)
    region = image[1::2, 8:-8]
    np.testing.assert_array_equal(_gray(region), region.astype(np.float32).mean(axis=2))


def test_notch_reduces_known_grid_without_changing_dc_or_unmasked_region():
    clean, observed = synthetic_frames()
    model = VideoReconstructor(observed, False, Settings(samples=8), roi=(16, 16, 224, 224))
    out = model.process(observed[3]).astype(float)
    inner = np.s_[40:-40, 40:-40]
    before = np.mean((observed[3][inner]-clean[3][inner])**2)
    after = np.mean((out[inner]-clean[3][inner])**2)
    assert after < before*0.06
    assert abs(np.mean(out[inner]-clean[3][inner])) < 1
    assert np.array_equal(out[:16], observed[3][:16])
    for field in model.spectral_fields:
        assert field.mask[0, 0] == 1


def test_interlaced_fields_remain_separate():
    clean, observed = synthetic_frames(interlaced=True)
    model = VideoReconstructor(observed, True, Settings(samples=8), roi=(0, 0, 256, 256))
    out = model.process(observed[2])
    for parity in range(2):
        a = out[40+parity:216:2, 40:216].astype(float)
        b = clean[2][40+parity:216:2, 40:216]
        assert np.mean(abs(a-b)) < 3
    assert out[41:216:2, 40:216].mean()-out[40:216:2, 40:216].mean() > 25


def test_core_reconstruction_is_finite_and_reduces_known_grid():
    clean, observed = synthetic_frames(size=192)
    model = VideoReconstructor(observed, False, Settings(samples=8, method="core"), roi=(0, 0, 192, 192))
    out = model.process(observed[4]).astype(float)
    inner = np.s_[32:-32, 32:-32]
    before = np.mean((observed[4][inner]-clean[4][inner])**2)
    after = np.mean((out[inner]-clean[4][inner])**2)
    assert np.isfinite(out).all()
    assert after < before*0.25


@pytest.mark.parametrize("interlaced", [False, True])
def test_metal_preserves_notch_filter_and_roi(interlaced):
    _, observed = synthetic_frames(interlaced=interlaced)
    model = VideoReconstructor(observed, interlaced, Settings(samples=8), roi=(16, 16, 224, 224))
    try:
        metal = metal_reconstructor(model)
    except MetalUnavailable as error:
        pytest.skip(str(error))
    frames = observed[2:6]
    results = [metal.process(frame) for frame in frames]
    for frame, gpu_result in zip(frames, results, strict=True):
        cpu_result = model.process(frame)
        np.testing.assert_array_equal(gpu_result[:16], frame[:16])
        assert np.max(np.abs(cpu_result.astype(np.int16)-gpu_result.astype(np.int16))) <= 1

    buffers = [frame.copy() for frame in frames]
    with ThreadPoolExecutor(max_workers=2) as executor:
        for actual, expected, buffer in zip(executor.map(metal.process_inplace, buffers), results, buffers, strict=True):
            assert actual is buffer
            np.testing.assert_array_equal(actual, expected)


def test_auto_backend_falls_back_to_cpu(monkeypatch):
    _, observed = synthetic_frames(size=128)
    model = VideoReconstructor(observed, False, Settings(samples=8), roi=(0, 0, 128, 128))

    def unavailable(_model):
        raise MetalUnavailable("unavailable")

    monkeypatch.setattr("endoscope_degrid.cli.metal_reconstructor", unavailable)
    assert _processing_backend(model, "auto") == ("cpu", model)
    with pytest.raises(MetalUnavailable):
        _processing_backend(model, "metal")


def test_strength_zero_is_exact_identity():
    _, observed = synthetic_frames(size=128)
    model = VideoReconstructor(observed, False, Settings(samples=8, strength=0), roi=(0, 0, 128, 128))
    assert np.array_equal(model.process(observed[1]), observed[1])


def test_no_grid_is_rejected_and_odd_interlaced_roi_is_rejected():
    plain = [np.full((128, 128, 3), 110, np.uint8)]*8
    with pytest.raises(CalibrationError):
        VideoReconstructor(plain, False, Settings(samples=8))
    with pytest.raises(ValueError, match="even"):
        VideoReconstructor(plain, True, Settings(samples=8), roi=(0, 1, 128, 126))


@pytest.mark.parametrize("settings", [Settings(strength=2), Settings(samples=2), Settings(bandwidth=0), Settings(workers=0)])
def test_invalid_parameters_fail(settings):
    with pytest.raises(ValueError):
        settings.validate()

