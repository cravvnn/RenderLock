"""Prepare NPR material packages for every explicitly selected scene entity.

This stage resolves and decodes inputs only. Blender pixel validation, surface
coverage and frame-dependent lighting must still succeed before application.
"""
from pathlib import Path
from .asset_converter import ConversionError, read_glb, sha
from .source_npr_assets import plan_npr_material_assets, decode_npr_material_assets
from .source_npr_package import build_npr_material_package, validate_npr_material_package
from .source_hero_lighting_package import canonical_sha


def material_set_key(row):
    return row.get('material_set_sha256',row['model_sha256'])


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
    if set(models)!={material_set_key(r) for r in selected.values()}:
        raise ConversionError('NPR model package coverage differs')
    for identity,row in selected.items():
        value=current[identity].get('model_file')
        if not isinstance(value,str) or not value:raise ConversionError('Current model file required')
        path=Path(value)
        if not path.is_absolute():path=Path(manifest_dir)/path
        if sha(path)!=row['model_sha256']:
            raise ConversionError('Selected model changed after NPR preparation')
        contract=current[identity].get('model_material_groups')
        if row.get('material_group_sha256')!=(contract or {}).get('content_sha256'):
            raise ConversionError('Selected material-group coverage changed after NPR preparation')
        if contract is not None:
            from .model_material_groups import validate_material_group_contract
            validate_material_group_contract(contract,current[identity].get('samples',[]),model=path)
        expected=canonical_sha(dict(model_sha256=row['model_sha256'],bundle_sha256=contract['bundle']['sha256'])) if contract and contract.get('bundle') else row['model_sha256']
        if material_set_key(row)!=expected:raise ConversionError('NPR material-set identity differs')
    for material_package in models.values():validate_npr_material_package(material_package)
    return {identity:models[material_set_key(row)] for identity,row in selected.items()}


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
        contract=entity.get('model_material_groups');key=digest
        if contract is not None:
            from .model_material_groups import validate_material_group_contract
            validate_material_group_contract(contract,entity.get('samples',[]),model=path)
            if contract.get('bundle'):key=canonical_sha(dict(model_sha256=digest,bundle_sha256=contract['bundle']['sha256']))
        if key not in models:
            document=read_glb(path)
            if contract is not None:
                from .model_material_groups import material_union_document
                document=material_union_document(document,contract)
            models[key]=(path,document)
        row=dict(entity_id=identity,model_sha256=digest,model_file=str(path))
        if key!=digest:row['material_set_sha256']=key
        if contract is not None:row['material_group_sha256']=contract['content_sha256']
        selections.append(row)
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
