"""Prepare NPR material packages for every explicitly selected scene entity.

This stage resolves and decodes inputs only. Blender pixel validation, surface
coverage and frame-dependent lighting must still succeed before application.
"""
from pathlib import Path
from .asset_converter import ConversionError, read_glb, sha
from .source_npr_assets import plan_npr_material_assets, decode_npr_material_assets
from .source_npr_package import build_npr_material_package, validate_npr_material_package


def validate_scene_npr_selection(package, entities, *, manifest_dir):
    """Bind prepared packages to current entity/model files before Blender edits."""
    if (type(package.get('schema_version')) is not int or package['schema_version']!=1
            or package.get('status')!='prepared_requires_blender_validation'
            or package.get('allow_partial') is not True):
        raise ConversionError('Unsupported scene NPR package')
    selected={}
    for row in package['entities']:
        identity=row['entity_id']
        if not isinstance(identity,str) or not identity or identity in selected:
            raise ConversionError('Duplicate or invalid prepared entity identity')
        selected[identity]=row
    current={}
    for entity in entities:
        identity=entity['id']
        if identity in current:raise ConversionError('Duplicate current entity identity')
        current[identity]=entity
    if not selected or set(current)!=set(selected):
        raise ConversionError('Prepared/current NPR entity coverage differs')
    models=package['models']
    if set(models)!={r['model_sha256'] for r in selected.values()}:
        raise ConversionError('NPR model package coverage differs')
    for identity,row in selected.items():
        value=current[identity].get('model_file')
        if not isinstance(value,str) or not value:raise ConversionError('Current model file required')
        path=Path(value)
        if not path.is_absolute():path=Path(manifest_dir)/path
        if sha(path)!=row['model_sha256']:
            raise ConversionError('Selected model changed after NPR preparation')
    for material_package in models.values():validate_npr_material_package(material_package)
    return {identity:models[row['model_sha256']] for identity,row in selected.items()}


def prepare_scene_npr_materials(segment, provider, decoder, *, manifest_dir,
                                allow_partial=False):
    if allow_partial is not True:
        raise ConversionError('Explicit partial NPR material preparation required')
    entities=segment.get('entities',[])
    if not entities:raise ConversionError('NPR preparation requires selected entities')
    identities=set();models={};selections=[]
    for entity in entities:
        identity=entity.get('id')
        if not isinstance(identity,str) or not identity or identity in identities:
            raise ConversionError('Distinct selected entity identities required')
        identities.add(identity)
        value=entity.get('model_file')
        if not isinstance(value,str) or not value:
            raise ConversionError('Selected entity has no converted NPR model')
        path=Path(value)
        if not path.is_absolute():path=Path(manifest_dir)/path
        path=path.resolve(strict=True)
        digest=sha(path)
        if digest not in models:models[digest]=(path,read_glb(path))
        selections.append(dict(entity_id=identity,model_sha256=digest,model_file=str(path)))
    # Resolve every selected model before decoding any texture. A later
    # unsupported material cannot leave a partially successful scene package.
    plans={digest:plan_npr_material_assets(document,provider,decoder)
           for digest,(_,document) in models.items()}
    packages={}
    for digest,plan in plans.items():
        decoded=decode_npr_material_assets(plan,provider,decoder)
        packages[digest]=build_npr_material_package(plan,decoded,provider,allow_partial=True)
    return dict(schema_version=1,status='prepared_requires_blender_validation',
                allow_partial=True,entities=selections,models=packages,
                limits=['Material inputs only; no shader application or coverage claim.',
                        'Converted model hash binds selection, not Source conversion fidelity.',
                        'Runtime primary/exposure/instance/depth inputs remain unqualified.'])
