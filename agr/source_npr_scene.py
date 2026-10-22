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


def _scene_material_selection(segment, *, manifest_dir):
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
    return selections,models


def _planning_failure(error, key, document, selections):
    """Add bounded selection evidence without replacing the original failure."""
    used={primitive.get('material') for mesh in document.get('meshes',[]) if isinstance(mesh,dict)
          for primitive in mesh.get('primitives',[]) if isinstance(primitive,dict)
          and type(primitive.get('material')) is int}
    materials=[]
    for index,material in enumerate(document.get('materials',[])):
        if index not in used or not isinstance(material,dict):continue
        extras=material.get('extras');vmat=extras.get('vmat') if isinstance(extras,dict) else None
        if isinstance(vmat,dict):materials.append(str(vmat.get('Name',''))[:256])
    materials=sorted(set(materials))
    entities=[row['entity_id'][:256] for row in selections if material_set_key(row)==key]
    existing=getattr(error,'details',None)
    details=dict(existing) if isinstance(existing,dict) else {}
    details.setdefault('code','selected_npr_material_preflight_failed')
    details['stage']='selected_npr_material_preflight'
    details['npr_selection']=dict(material_set_sha256=key,entity_count=len(entities),
        entity_ids=entities[:8],material_count=len(materials),materials=materials[:8])
    error.details=details


def plan_scene_npr_materials(segment, provider, decoder, *, manifest_dir,
                             allow_partial=False):
    """Resolve the exact selected material union without decoding any texture."""
    if allow_partial is not True:
        raise ConversionError('Explicit partial NPR material preparation required')
    selections,models=_scene_material_selection(segment,manifest_dir=manifest_dir)
    plans={}
    for key,(_,document) in models.items():
        try:
            plans[key]=plan_npr_material_assets(document,provider,decoder)
        except Exception as error:
            _planning_failure(error,key,document,selections)
            raise
    result=dict(schema_version=1,status='planned_requires_decode',allow_partial=True,
                entities=selections,models=plans,
                document_sha256={key:canonical_sha(document) for key,(_,document) in models.items()})
    result['content_sha256']=canonical_sha(result)
    return result


def _validate_reusable_plan(plan, segment, provider, *, manifest_dir):
    if (not isinstance(plan,dict) or type(plan.get('schema_version')) is not int
            or plan['schema_version']!=1 or plan.get('status')!='planned_requires_decode'
            or plan.get('allow_partial') is not True
            or plan.get('content_sha256')!=canonical_sha({k:v for k,v in plan.items() if k!='content_sha256'})):
        raise ConversionError('Unsupported or changed scene NPR material plan')
    selections,models=_scene_material_selection(segment,manifest_dir=manifest_dir)
    def identities(rows):
        result={}
        for row in rows:
            identity=row['entity_id']
            if not isinstance(identity,str) or not identity or identity in result:
                raise ConversionError('Duplicate or invalid planned entity identity')
            result[identity]={k:v for k,v in row.items() if k!='model_file'}
        return result
    if identities(plan['entities'])!=identities(selections):
        raise ConversionError('Planned/current NPR entity or material-group identity differs')
    if (set(plan['models'])!=set(models) or plan.get('document_sha256')!={
            key:canonical_sha(document) for key,(_,document) in models.items()}):
        raise ConversionError('Planned/current NPR selected material union differs')
    # Recheck ALL installed material inputs before the first texture decode.
    # Package construction still enforces current evaluator and texture guards.
    from .model_material_groups import used_materials
    for key,material_plan in plan['models'].items():
        rows=material_plan.get('materials')
        if not isinstance(rows,list) or not 1<=len(rows)<=64:
            raise ConversionError('Expected bounded planned NPR material sources')
        if (len({row['material'] for row in rows})!=len(rows)
                or {row['material'] for row in rows}!=set(used_materials(models[key][1]))):
            raise ConversionError('Planned/current NPR material source coverage differs')
        try:
            for row in rows:
                if provider.resolve(row['material']).sha256!=row['source_sha256']:
                    raise ConversionError('NPR material source changed since planning')
        except Exception as error:
            _planning_failure(error,key,models[key][1],selections)
            raise
    return selections


def prepare_scene_npr_materials(segment, provider, decoder, *, manifest_dir,
                                allow_partial=False, plan=None):
    if allow_partial is not True:
        raise ConversionError('Explicit partial NPR material preparation required')
    if plan is None:
        plan=plan_scene_npr_materials(segment,provider,decoder,
                                      manifest_dir=manifest_dir,allow_partial=True)
        selections=plan['entities']
    else:
        selections=_validate_reusable_plan(plan,segment,provider,manifest_dir=manifest_dir)
    # Every selected model has been planned before decoding any texture.
    packages={}
    for digest,material_plan in plan['models'].items():
        decoded=decode_npr_material_assets(material_plan,provider,decoder)
        packages[digest]=build_npr_material_package(material_plan,decoded,provider,allow_partial=True)
    return dict(schema_version=1,status='prepared_requires_blender_validation',
                allow_partial=True,entities=selections,models=packages,
                limits=['Material inputs only; no shader application or coverage claim.',
                        'Converted model hash binds selection, not Source conversion fidelity.',
                        'Runtime primary/exposure/instance/depth inputs remain unqualified.'])
