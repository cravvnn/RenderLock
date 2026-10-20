"""Static pbr color expressions qualified against installed material built-ins.

These are row-vector shader matrices. The squared normalized luminance weights
follow the built-in's luminance scaling/rotation/unscaling sequence; substituting
standard grayscale weights changes its output. Runtime instance color and the
texture-dependent pivot for nonunit contrast remain separate inputs.
"""
import numpy as np

LUMINANCE=np.asarray([.2125,.7154,.0721],dtype=float)
WEIGHTS=LUMINANCE**2/np.sum(LUMINANCE**2)


def saturation_brightness(saturation,brightness):
    if (type(saturation) not in (int,float) or type(brightness) not in (int,float)
            or not np.isfinite([saturation,brightness]).all()
            or not 0<=saturation<=4 or not 0<=brightness<=4):
        raise ValueError('Finite authored saturation/brightness in0..4 required')
    matrix=np.eye(4)
    matrix[:3,:3]=(saturation*np.eye(3)+(1-saturation)*WEIGHTS[:,None])*brightness
    return matrix


def color_tint(rgb,mode):
    rgb=np.asarray(rgb,dtype=float)
    if (rgb.shape!=(3,) or not np.isfinite(rgb).all() or np.any(rgb<0) or np.any(rgb>1)
            or type(mode) is not int or mode not in (0,1,2)):
        raise ValueError('Finite authored RGB tint and mode0/1/2 required')
    if mode in (0,2):return np.diag([*(rgb*(2 if mode==2 else 1)),1])
    # Qualified shader expression MatrixColorTint3(rgb,.85,mode).
    maximum=float(np.max(rgb));chroma=0 if maximum==0 else (maximum-float(np.min(rgb)))/maximum
    transverse=1-chroma;longitudinal=1-chroma*chroma*.85
    matrix=np.eye(4)
    matrix[:3,:3]=transverse*np.eye(3)+(longitudinal-transverse)*WEIGHTS[:,None]
    matrix[3,:3]=rgb-rgb@matrix[:3,:3]
    return matrix


def color_correction(csb,pivot):
    csb=np.asarray(csb,dtype=float);pivot=np.asarray(pivot,dtype=float)
    if (csb.shape!=(3,) or pivot.shape!=(3,) or not np.isfinite([csb,pivot]).all()
            or np.any(csb<0) or np.any(csb>4) or np.any(pivot<0) or np.any(pivot>1)):
        raise ValueError('Bounded authored color controls and authenticated texture average required')
    matrix=saturation_brightness(float(csb[1]),float(csb[2]))
    matrix[3,:3]=(1-csb[0])*pivot@matrix[:3,:3]
    matrix[:3,:3]*=csb[0]
    return matrix
