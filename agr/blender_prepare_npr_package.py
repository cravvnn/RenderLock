"""Explicit partial material-package preparation after qualified generic lighting.

Caller qualifies converted raw color images and evaluated probe coverage. This
does not supply frame-dependent primary/depth/rim or certify runtime overrides.
"""
import numpy as np
from agr.source_npr_package import validate_npr_material_package
from agr.blender_npr_package import validate_npr_pixels
from agr.npr_authored_albedo import neutral_tint_controls
from agr.npr_material_identity import material_resource,pbr_material_resource, compiled_resource
from agr.source_material_routes import material_route,NPR_ROUTE


def prepare_material_package(hero_package, material_package, primary, *, materials,
                             objects, world_sha256, manifest_dir,
                             allow_partial=False, assume_white_instance_color=False,
                             actor_id=None, probe_ids=None):
    if allow_partial is not True or assume_white_instance_color is not True:
        raise ValueError('Explicit partial NPR and white instance-color assumptions required')
    materials=list(materials);objects=list(objects)
    package_rows=validate_npr_material_package(material_package)
    if {pbr_material_resource(m) for m in materials}!=set(package_rows):
        raise ValueError('NPR package/selected material coverage mismatch')
    npr_materials=[m for m in materials if material_route(package_rows[pbr_material_resource(m)])==NPR_ROUTE]
    preserved=[]
    for mat in materials:
        row=package_rows[pbr_material_resource(mat)]
        if mat not in npr_materials:
            if mat.get('agr_source_hero_lighting')!='partial_indirect_reference':
                raise ValueError('Generic material lacks prepared Source route')
            preserved.append(dict(material=mat.name,source_material=pbr_material_resource(mat),
                route=material_route(row),status='preserved_generic_reference',
                runtime=row.get('runtime'),fidelity_gaps=row.get('fidelity_gaps',[])))
            if 'expression_evaluation' in row:preserved[-1]['expression_evaluation']=row['expression_evaluation']
    controls={};textures={};no_specular={}
    for mat in npr_materials:
        name=material_resource(mat);row=package_rows[name];authored=row['authored']
        source={}
        for target,key,value in [('IntParams','m_intParams','m_nValue'),
                                 ('FloatParams','m_floatParams','m_flValue'),
                                 ('VectorParams','m_vectorParams','m_value')]:
            entries=authored[key]
            source[target]={r['m_name']:r[value] for r in entries}
            if len(source[target])!=len(entries):raise ValueError('Duplicate authored parameter')
        average=row.get('albedo_average')
        pivot=None
        if average is not None:
            from .source_texture_average import validate_texture_average
            pivot=validate_texture_average(average)
            if compiled_resource(mat['vmat']['TextureParams'].get('g_tColor'),'.vtex')!=average['resource']:
                raise ValueError('Authored contrast color texture identity differs')
        converted_parameters=mat['vmat']
        expression=row.get('expression_evaluation')
        if expression:
            from .material_expressions import apply_vector_overrides
            source=apply_vector_overrides(source,expression)
            converted_parameters=apply_vector_overrides({key:dict(mat['vmat'].get(key,{})) for key in ('IntParams','FloatParams','VectorParams')},expression)
        expected=neutral_tint_controls(source,assume_white_instance_color=True,
                                       shader_sha256=material_package['shader_sha256'],texture_average=pivot)
        converted=neutral_tint_controls(converted_parameters,assume_white_instance_color=True,
                                        shader_sha256=material_package['shader_sha256'],texture_average=pivot)
        if any(not np.array_equal(expected[k],converted[k]) for k in expected):
            raise ValueError('Converted/authored albedo controls differ')
        for parameter,texture in row['textures'].items():
            actual=mat['vmat']['TextureParams'].get(parameter)
            if compiled_resource(actual,'.vtex')!=texture['resource']:
                raise ValueError('Converted NPR texture resource differs')
        controls[name]=expected
        no_specular[name]=bool(source['IntParams'].get('F_NO_SPECULAR_AT_FULL_ROUGHNESS',0))
        controls[name]['mask_texture']=row['textures']['g_tTintMaskRimLightMask']
        texture=row['textures']['g_tNprTransmissiveColor']
        textures[name]=dict(material=name,resource=texture['resource'],pixels=texture)
    # Temporary pixel validation images are removed even on failure.
    pixels=validate_npr_pixels(material_package)
    from agr.blender_prepare_npr import prepare
    rows=prepare(hero_package,textures,primary,materials=npr_materials,objects=objects,
                 world_sha256=world_sha256,manifest_dir=manifest_dir,
                 allow_partial=True,albedo_controls=controls,
                 actor_id=actor_id,probe_ids=probe_ids,no_specular=no_specular,
                 complete_materials=materials) if npr_materials else []
    for row in rows:
        source=package_rows[row['source_material']]
        row.update(route=NPR_ROUTE,status='applied_npr_reference',runtime=source.get('runtime'),
                   fidelity_gaps=source.get('fidelity_gaps',[]))
        if 'expression_evaluation' in source:row['expression_evaluation']=source['expression_evaluation']
    return dict(materials=rows+preserved,pixels=pixels,
                assumptions=['Omitted albedo controls resolved from the qualified pbr shader metadata defaults.',
                             'White instance color; network-to-GPU binding unproven.',
                             'Reference primary/exposure/DFAO/screen inputs.'],
                limits='Caller-qualified converted color images/probe coverage; frame-specific rim not applied.')
