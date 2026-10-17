"""Create packed Blender data images from a validated hero lighting package."""
import numpy as np
from .source_hero_lighting_package import validate_hero_lighting_package
from .source_cube_sampling import _tap
from .source_brdf_assets import read_layer


def padded_cube(faces):
    if faces.ndim!=4 or faces.shape[0]!=6 or faces.shape[1]!=faces.shape[2] or faces.shape[3]!=4 or faces.dtype!=np.float32:
        raise ValueError('Expected six float32 cube faces')
    n=faces.shape[1];stack=np.empty((6,n+2,n+2,4),dtype='f4');stack[:,1:-1,1:-1]=faces
    for face in range(6):
        for y in range(-1,n+1):
            for x in (-1,n):stack[face,y+1,x+1]=_tap(faces,face,x,y)
        for x in range(n):
            for y in (-1,n):stack[face,y+1,x+1]=_tap(faces,face,x,y)
    return stack.reshape(6*(n+2),n+2,4)


def prepare_images(package,world_sha256,manifest_dir):
    import bpy
    resolved=validate_hero_lighting_package(package,world_sha256,manifest_dir)
    def image(name,pixels):
        height,width,_=pixels.shape
        if max(width,height)>16384:raise ValueError('Probe data image exceeds qualified dimensions')
        result=bpy.data.images.new(name,width=width,height=height,alpha=True,float_buffer=True)
        result.colorspace_settings.name='Non-Color';result.alpha_mode='CHANNEL_PACKED'
        result.pixels.foreach_set(pixels.reshape(-1));result.pack();result.use_fake_user=True
        return result
    atlas={}
    for row in package['atlas']:
        width,height,depth=row['size']
        pixels=np.fromfile(resolved['atlas'][row['name']],dtype='<f4').reshape(height*depth,width,4)
        atlas[row['name']]=(image('AGR probe '+row['name'],pixels),row)
    cubes={}
    for resource,index in sorted(resolved['coefficients']):
        levels=[]
        for mip in range(7):
            n=256>>mip
            faces=np.array([np.fromfile(resolved['cubes'][(resource,index,f,mip)],dtype='<f4').reshape(n,n,4) for f in range(6)])
            levels.append(image(f'AGR cube {index} mip {mip}',padded_cube(faces)))
        cubes[(resource,index)]=levels
    brdf=image('AGR hero BRDF layer1',read_layer(resolved['brdf']))
    return dict(atlas=atlas,cubes=cubes,coefficients=resolved['coefficients'],brdf=brdf)
