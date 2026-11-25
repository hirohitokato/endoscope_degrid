"""Research experiment; not a finished video pipeline."""
import json
import time
from pathlib import Path

import av
import cv2
import numpy as np

ROOT = Path(__file__).parent / "results"
ROOT.mkdir(exist_ok=True)
ROI = (532, 102, 896, 896)


def spectrum_mask(samples, width=0.018):
    h, w = samples.shape[1:3]
    fy, fx = np.meshgrid(np.fft.fftfreq(h), np.fft.fftfreq(w), indexing="ij")
    # Samples are one field: express frequencies per full-frame pixel.
    fy = fy / 2
    radius = np.hypot(fx, fy)
    bins = np.rint(radius * w).astype(int)
    win = np.outer(np.hanning(h), np.hanning(w))
    powers = []
    for im in samples:
        gray = im.astype(np.float32).mean(axis=2)
        p = abs(np.fft.fft2((gray - gray.mean()) * win)) ** 2
        powers.append(p / max(p.sum(), 1e-20))
    power = np.median(powers, axis=0)
    bg = np.zeros(bins.max()+1)
    for i in range(len(bg)):
        vals = power[bins == i]
        bg[i] = np.median(vals) if len(vals) else 1e-20
    score = 10*np.log10(np.maximum(power, 1e-30)/np.maximum(bg[bins], 1e-30))
    score[radius < 0.055] = -100
    peaks = []
    mask = np.ones((h, w), np.float64)
    for _ in range(60):
        y, x = np.unravel_index(np.argmax(score), score.shape)
        s = score[y, x]
        if s < 12:
            break
        px, py = fx[y, x], fy[y, x]
        peaks.append((float(px), float(py), float(s)))
        dx = np.abs(fx - px); dx = np.minimum(dx, 1-dx)
        dy = np.abs(fy - py); dy = np.minimum(dy, 0.5-dy)
        ds = dx**2+dy**2
        score[ds < 0.023**2] = -100
        d = ds/width**2
        mask *= d**3/(d**3+1)
    # Exact Hermitian symmetry including Nyquist bins.
    mirror = mask[np.ix_((-np.arange(h)) % h, (-np.arange(w)) % w)]
    mask = np.minimum(mask, mirror)
    mask[radius < 0.045] = 1
    return mask.astype(np.float32), peaks


def notch(im, mask):
    return np.fft.ifft2(np.fft.fft2(im, axes=(0, 1))*mask[:, :, None], axes=(0, 1)).real.astype(np.float32)


def estimate_gain(samples, mask):
    residuals=[]
    for im in samples:
        log = np.log(im.astype(np.float32)/255 + 0.025)
        residuals.append(log-notch(log, mask))
    loggain = np.median(residuals, axis=0)
    loggain -= notch(loggain, mask)
    return np.exp(np.clip(loggain, -1.8, 1.8))


def gradient(a):
    dx = np.zeros_like(a); dy = np.zeros_like(a)
    dx[:, :-1] = a[:, 1:] - a[:, :-1]
    dy[:-1] = (a[1:] - a[:-1])/2
    return dx, dy


def divergence(px, py):
    d = px.copy()
    d[:, 1:] -= px[:, :-1]
    d += py/2
    d[1:] -= py[:-1]/2
    return d


def inverse_tv(im, gain, initial, lam=0.012, iterations=160):
    y = im.astype(np.float32)/255
    x = np.clip(initial/255, 0, 1).copy(); xbar=x.copy()
    px=np.zeros_like(x); py=np.zeros_like(x)
    for _ in range(iterations):
        dx,dy=gradient(xbar)
        px += 0.3*dx; py += 0.3*dy
        norm = np.maximum(1, np.sqrt(px*px+py*py)/lam)
        px/=norm;py/=norm
        old=x
        x=np.clip((x+0.3*divergence(px,py)+0.3*gain*y)/(1+0.3*gain*gain),0,1)
        xbar=2*x-old
    return x*255


def load_samples(path, name):
    cache=ROOT/f'{name}_full_samples.npy'
    if cache.exists():return np.load(cache)
    frames=[]
    with av.open(path) as c:
        v=c.streams.video[0];duration=float(v.duration*v.time_base)
        for t in np.linspace(1,duration-1,24):
            c.seek(int(t/v.time_base),stream=v,backward=True)
            for f in c.decode(v):
                if f.time >= t:
                    frames.append(f.to_ndarray(format='bgr24'));break
    result=np.asarray(frames);np.save(cache,result);return result


def run():
    results=[]
    for name, ident, targets in [
        ('first','60fac7e7a2ec4af787b24a0a9edcbe51',[5,15]),
        ('second','45b496b6de6e496ab4d4591ecdca49a2',[9,18]),
    ]:
        path=f'/Users/a3140236/develop/works/airway_scope/data/videos/{ident}/source.mp4'
        samples=load_samples(path,name)
        x,y,w,h=ROI;crop=samples[:,y:y+h,x:x+w]
        configs=[]
        for parity in range(2):
            fields=crop[:,parity::2]
            mask,peaks=spectrum_mask(fields)
            gain=estimate_gain(fields,mask)
            configs.append((mask,gain))
            cv2.imwrite(str(ROOT/f'{name}_gain_{parity}.png'),np.rint(np.clip(gain/2*255,0,255)).astype(np.uint8))
        for k in targets:
            raw=crop[k];filtered=np.empty_like(raw);restored=np.empty_like(raw)
            times=[]
            for mode in ['notch','tv']:
                st=time.perf_counter()
                for parity,(mask,gain) in enumerate(configs):
                    field=raw[parity::2].astype(np.float32)
                    nf=notch(field,mask)
                    if mode=='notch':filtered[parity::2]=np.rint(np.clip(nf,0,255)).astype(np.uint8)
                    else:restored[parity::2]=np.rint(inverse_tv(field,gain,nf)).astype(np.uint8)
                times.append(time.perf_counter()-st)
            panes=[]
            for title,im in [('Original',raw),('A: spectral notches',filtered),('B: gain + TV inverse',restored)]:
                cv2.imwrite(str(ROOT/f'{name}_{k}_{title[0]}.png'),im)
                # Matched center crop at native scale, with full FOV underneath.
                tile=im[240:640,240:640]
                tile=cv2.copyMakeBorder(tile,30,0,0,0,cv2.BORDER_CONSTANT)
                cv2.putText(tile,title,(10,22),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
                full=cv2.resize(im,(400,400))
                panes.append(np.concatenate([tile,full],axis=0))
            cv2.imwrite(str(ROOT/f'{name}_{k}_methods.png'),np.concatenate(panes,axis=1))
            results.append({'video':name,'sample':k,'notch_seconds':times[0],'inverse_seconds':times[1]})
            print(results[-1],flush=True)
    (ROOT/'comparison_timing.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__':run()
