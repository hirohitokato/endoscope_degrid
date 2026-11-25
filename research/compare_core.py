"""Independent core-sampling reconstruction experiment (no image blur)."""
import sys

import json
import time
import numpy as np
import cv2
from scipy.spatial import Delaunay
from scipy.interpolate import CloughTocher2DInterpolator
from compare_methods import ROOT, ROI, spectrum_mask, estimate_gain


def core_centers(gain, spacing=12.0):
    # Greedy geometric nonmaximum selection; no raster morphology.
    img=gain.mean(axis=2)
    h,w=img.shape;allowed=np.ones((h,w),bool)
    border=8
    allowed[:border]=False;allowed[-border:]=False
    allowed[:,:border]=False;allowed[:,-border:]=False
    points=[];rr=spacing*0.68;r=int(np.ceil(rr))
    yy,xx=np.mgrid[-r:r+1,-r:r+1];disk=(xx**2+(2*yy)**2)<rr**2
    for index in np.argsort(img.ravel())[::-1]:
        y,x=divmod(int(index),w)
        if img[y,x]<1.025:break
        if not allowed[y,x]:continue
        points.append((x,2*y))
        y0,y1=max(0,y-r),min(h,y+r+1);x0,x1=max(0,x-r),min(w,x+r+1)
        allowed[y0:y1,x0:x1]&=~disk[y0-y+r:y1-y+r,x0-x+r:x1-x+r]
    return np.asarray(points,float)


def core_values(im,gain,points,r=2.7):
    vals=np.zeros((len(points),3));norm=np.zeros_like(vals)
    cx=points[:,0].astype(int);cy=(points[:,1]/2).astype(int)
    for dy in range(-2,3):
        for dx in range(-3,4):
            if dx*dx+(dy*2)**2>r*r:continue
            y=cy+dy;x=cx+dx
            vals+=gain[y,x]*im[y,x];norm+=gain[y,x]**2
    return vals/np.maximum(norm,1e-10)


timing=[]
for name,targets in [('first',[5,15]),('second',[9,18])]:
    x,y,w,h=ROI;samples=np.load(ROOT/f'{name}_full_samples.npy')[:,y:y+h,x:x+w]
    configs=[]
    for parity in range(2):
        mask,peaks=spectrum_mask(samples[:,parity::2]);gain=estimate_gain(samples[:,parity::2],mask)
        points=core_centers(gain);tri=Delaunay(points)
        hh,ww=gain.shape[:2];yy,xx=np.mgrid[:hh,:ww];query=np.column_stack([xx.ravel(),2*yy.ravel()])
        simplex=tri.find_simplex(query);valid=simplex>=0
        tf=tri.transform[simplex[valid]]
        bary=np.einsum('ijk,ik->ij',tf[:,:2],query[valid]-tf[:,2]);bary=np.column_stack([bary,1-bary.sum(axis=1)])
        configs.append((gain,points,tri,query,valid,simplex,bary))
        print(name,parity,'cores',len(points),flush=True)
    for k in targets:
        raw=samples[k];dest=raw.copy();start=time.perf_counter()
        for parity,(gain,points,tri,query,valid,simplex,bary) in enumerate(configs):
            vals=core_values(raw[parity::2].astype(np.float32),gain,points)
            interp=CloughTocher2DInterpolator(tri,vals)
            recon=interp(query).reshape(gain.shape)
            ok=np.isfinite(recon).all(axis=2)
            field=dest[parity::2];field[ok]=np.rint(np.clip(recon[ok],0,255)).astype(np.uint8)
        elapsed=time.perf_counter()-start
        cv2.imwrite(str(ROOT/f'{name}_{k}_C.png'),dest)
        panels=[]
        for title,im in [('Original',raw),('A: spectral notches',cv2.imread(str(ROOT/f'{name}_{k}_A.png'))),('B: gain + TV inverse',cv2.imread(str(ROOT/f'{name}_{k}_B.png'))),('C: core reconstruction',dest)]:
            tile=im[240:640,240:640];tile=cv2.copyMakeBorder(tile,30,0,0,0,cv2.BORDER_CONSTANT)
            cv2.putText(tile,title,(10,22),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
            panels.append(np.concatenate([tile,cv2.resize(im,(400,400))]))
        cv2.imwrite(str(ROOT/f'{name}_{k}_all_methods.png'),np.concatenate(panels,axis=1))
        timing.append({'video':name,'sample':k,'core_seconds':elapsed});print(timing[-1],flush=True)
(ROOT/'core_timing.json').write_text(json.dumps(timing,indent=2))
