"""Validate decoded NPR pixels without assigning materials or retaining images."""
import numpy as np
from .source_npr_package import validate_npr_material_package
from .asset_converter import ConversionError


def validate_npr_pixels(package):
    import bpy
    from .blender_images import load_packed_image
    materials=validate_npr_material_package(package)
    rows=[]
    for name,row in materials.items():
        result=dict(material=name,textures={})
        for parameter,texture in row['textures'].items():
            image=load_packed_image(texture['path'],expected_sha256=texture['sha256'])
            try:
                # Inspect encoded channels, preserving RGB when stored alpha=0.
                image.colorspace_settings.name='Non-Color';image.alpha_mode='CHANNEL_PACKED'
                if list(image.size)!=texture['size']:raise ConversionError('Blender NPR texture dimensions differ')
                pixels=np.empty(len(image.pixels),dtype=np.float32);image.pixels.foreach_get(pixels)
                pixels=pixels.reshape(-1,4)
                if not np.isfinite(pixels).all() or np.any(pixels<0) or np.any(pixels>1):
                    raise ConversionError('Invalid normalized NPR texture pixels')
                summary=dict(sha256=texture['sha256'],size=texture['size'],minimum=pixels.min(axis=0).tolist(),maximum=pixels.max(axis=0).tolist())
                if parameter=='g_tTintMaskRimLightMask':
                    for channel,index in [('rim',1),('tint',0)]:
                        constant=bool(np.all(pixels[:,index]==pixels[0,index]))
                        summary[channel+'_constant']=constant
                        if constant:summary['linear_'+channel+'_mask']=float(pixels[0,index])
                result['textures'][parameter]=summary
            finally:bpy.data.images.remove(image)
        rows.append(result)
    return dict(status='validated_decoded_pixels',materials=rows,
                scope='Normalized encoded PNG channels checked; sRGB transmissive and linear red/green masks. Spatial channels remain textures, never a first-pixel substitute. No hardware filtering or runtime shader parity proof.')
