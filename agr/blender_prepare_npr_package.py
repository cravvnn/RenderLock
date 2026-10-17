"""Explicit partial material-package preparation after qualified generic lighting.

Caller qualifies converted raw color images and evaluated probe coverage. This
does not supply frame-dependent primary/depth/rim or certify runtime overrides.
"""
import numpy as np
from agr.source_npr_package import validate_npr_material_package
from agr.blender_npr_package import validate_npr_pixels
from agr.npr_authored_albedo import neutral_tint_controls
from agr.npr_material_identity import material_resource, compiled_resource


def prepare_material_package(hero_package, material_package, primary, *, materials,
                             objects, world_sha256, manifest_dir,
                             allow_partial=False, assume_white_instance_color=False):
    if allow_partial is not True or assume_white_instance_color is not True:
        raise ValueError('Explicit partial NPR and white instance-color assumptions required')
    materials=list(materials);objects=list(objects)
    package_rows=validate_npr_material_package(material_package)
    if {material_resource(m) for m in materials}!=set(package_rows):
        raise ValueError('NPR package/selected material coverage mismatch')
    controls={};textures={}
    for mat in materials:
        name=material_resource(mat);row=package_rows[name];authored=row['authored']
        source={}
        for target,key,value in [('IntParams','m_intParams','m_nValue'),
                                 ('FloatParams','m_floatParams','m_flValue'),
                                 ('VectorParams','m_vectorParams','m_value')]:
            entries=authored[key]
            source[target]={r['m_name']:r[value] for r in entries}
            if len(source[target])!=len(entries):raise ValueError('Duplicate authored parameter')
        expected=neutral_tint_controls(source,assume_white_instance_color=True)
        converted=neutral_tint_controls(mat['vmat'],assume_white_instance_color=True)
        if any(not np.array_equal(expected[k],converted[k]) for k in expected):
            raise ValueError('Converted/authored albedo controls differ')
        for parameter,texture in row['textures'].items():
            actual=mat['vmat']['TextureParams'].get(parameter)
            if compiled_resource(actual,'.vtex')!=texture['resource']:
                raise ValueError('Converted NPR texture resource differs')
        controls[name]=expected
        texture=row['textures']['g_tNprTransmissiveColor']
        textures[name]=dict(material=name,resource=texture['resource'],pixels=texture)
    # Temporary pixel validation images are removed even on failure.
    pixels=validate_npr_pixels(material_package)
    from agr.blender_prepare_npr import prepare
    rows=prepare(hero_package,textures,primary,materials=materials,objects=objects,
                 world_sha256=world_sha256,manifest_dir=manifest_dir,
                 allow_partial=True,albedo_controls=controls)
    return dict(materials=rows,pixels=pixels,
                assumptions=['White instance color; network-to-GPU binding unproven.',
                             'Reference primary/exposure/DFAO/screen inputs.'],
                limits='Caller-qualified converted color images/probe coverage; frame-specific rim not applied.')
