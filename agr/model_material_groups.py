"""Prepare exact material-group identities without replacing model geometry."""
import copy
import json
import os
from pathlib import Path
import subprocess

from .asset_converter import ConversionError, compiled, read_glb, sha
from .game_assets import safe_resource_path
from .map_data import dump_map_data
from .source_hero_lighting_package import canonical_sha
from .source_material_groups import material_group_table, material_group_track

PARSER_SHA256='cbc14ee90f4fa7481448721c6e0a66a1c153ccd191f9c1deb977c3abb6466cd6'


def used_materials(document):
    materials=document.get('materials',[]);result={}
    for mesh in document.get('meshes',[]):
        for primitive in mesh.get('primitives',[]):
            index=primitive.get('material')
            if type(index) is not int or not 0<=index<len(materials):raise ConversionError('Exact primitive material identity required')
            material=materials[index];name=material.get('extras',{}).get('vmat',{}).get('Name')
            if not isinstance(name,str) or not name.endswith('.vmat'):raise ConversionError('Full converted VMAT identity required')
            name=safe_resource_path(compiled(name))
            if name in result and result[name]!=material:raise ConversionError('Conflicting converted VMAT identities')
            result[name]=material
    if not 1<=len(result)<=64:raise ConversionError('Expected1..64 used model materials')
    return result


def _bundle(resources,provider,decoder,*,command=None,environment=None,unlit_only=False):
    if command is None:
        from .reconstructed_cloth import discover_extractor
        command,environment=discover_extractor()
    command=list(command);tool=Path(command[-1]);parser=tool.with_name('ValveResourceFormat.dll')
    if not parser.is_file() or sha(parser)!=PARSER_SHA256:raise ConversionError('Material-group conversion requires the qualified model extractor')
    names=set(resources);shader_names=set()
    for resource in resources:
        data=dump_map_data(decoder,resource,provider);shader=data.get('m_shaderName')
        # Other shader families need an independently qualified adapter.
        if shader!='pbr.vfx':raise ConversionError('Material-group variant uses an unqualified shader family: '+str(shader))
        shader_names.add(shader)
        for row in data.get('m_textureParams',[]):
            name=safe_resource_path(compiled(row['m_pValue']))
            if not name.endswith('.vtex_c'):raise ConversionError('Variant texture identity required')
            names.add(name)
    shader_files=['shaders/vfx/pbr_pc_50_'+kind+'.vcs' for kind in ('features','ps','vs')]
    names.update(shader_files);rows=[];total=0
    from .source_npr_assets import validate_npr_decode_source
    for name in sorted(names):
        entry=provider.lookup(name)
        if entry is None or not 0<entry.length+len(entry.preload)<=32*1024*1024:
            raise ConversionError('Material-group source is missing or exceeds32MiB')
        source=provider.resolve(name);size=source.local_path.stat().st_size;total+=size
        if total>256*1024*1024 or len(rows)>=512:raise ConversionError('Material-group source set exceeds bound')
        if name.endswith('.vtex_c'):validate_npr_decode_source(source.local_path)
        rows.append(dict(resource=name,path=str(source.local_path.resolve()),sha256=source.sha256,bytes=size))
    request=dict(schema_version=1,materials=sorted(resources),files=rows,
        shaders=[dict(name=name,files=shader_files) for name in sorted(shader_names)])
    settings=dict(adapter_sha256=sha(Path(__file__)),probe_sha256=sha(tool),parser_sha256=PARSER_SHA256,unlit_only=unlit_only)
    if unlit_only:
        from . import gltf_materials
        settings['unlit_adapter_sha256']=sha(Path(gltf_materials.__file__))
    # Native pixel conversion is part of the immutable input identity as well.
    from .helper_files import native_image_path
    native=native_image_path(tool.parent)
    settings['native_image_sha256']=sha(native)
    env=dict(os.environ if environment is None else environment)
    env['PATH']=str(native.parent)+os.pathsep+env.get('PATH','')
    if native.suffix == '.so':
        env['LD_LIBRARY_PATH']=str(native.parent)+(os.pathsep+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    def build(temp):
        request_path=temp/'request.json';request_path.write_text(json.dumps(request,allow_nan=False),encoding='utf-8')
        result=temp/'result.json';output=temp/'materials.glb'
        proc=subprocess.run(command+['--material-bundle-v1',str(request_path.resolve()),str(output.resolve()),str(result.resolve())],
            capture_output=True,env=env,timeout=180)
        (temp/'export.log').write_bytes(proc.stdout+proc.stderr)
        if proc.returncode or b'WARNING' in proc.stdout or b'WARNING' in proc.stderr:
            raise ConversionError('Exact material-group conversion failed: '+(proc.stdout+proc.stderr)[-2000:].decode('utf-8',errors='replace'))
        report=json.loads(result.read_text(encoding='utf-8'))
        if (report.get('status')!='converted_materials_only' or report.get('request_sha256')!=sha(request_path)
                or report.get('parser_sha256')!=PARSER_SHA256 or report.get('bundle_sha256')!=sha(output)
                or [r['resource'] for r in report.get('materials',[])]!=sorted(resources)):
            raise ConversionError('Material-group exporter completion identity differs')
        if unlit_only:
            from .gltf_materials import repair_glb
            repair_glb(output,temp/'materials-unlit.glb',unlit_only=True)
    directory=decoder._entry('material_bundle_v1',{row['resource']:row['sha256'] for row in rows},settings,build)
    path=directory/('materials-unlit.glb' if unlit_only else 'materials.glb')
    document=read_glb(path)
    if (any(document.get(key) for key in ('meshes','nodes','skins','animations'))
            or {safe_resource_path(compiled(m.get('extras',{}).get('vmat',{}).get('Name',''))) for m in document.get('materials',[])}!=set(resources)):
        raise ConversionError('Material bundle geometry or exact material coverage differs')
    return dict(path=str(path),sha256=sha(path),materials=sorted(resources),parser_sha256=PARSER_SHA256)


def prepare_model_material_groups(model,resource,entity,provider,decoder,*,command=None,environment=None,unlit_only=False):
    source=provider.resolve(resource);data=dump_map_data(decoder,resource,provider)
    groups=material_group_table(data);track=material_group_track(groups,entity.get('samples',[]))
    base=used_materials(read_glb(model));selected=set()
    for row in track['selections']:
        selected.update(target for name,target in row['replacements'].items() if name in base)
    selected-=set(base)
    if len(set(base)|selected)>64:raise ConversionError('Selected material-group union exceeds64 model materials')
    bundle=_bundle(selected,provider,decoder,command=command,environment=environment,unlit_only=unlit_only) if selected else None
    result=dict(schema_version=1,mode='source_material_group_slots',model_sha256=sha(model),source_sha256=source.sha256,
        groups=groups,track=track,base_materials=sorted(base),bundle=bundle,
        scope='Recorded uint32 material token resolves exact per-original-resource slots; mesh-group visibility and skeletal skins remain independent.')
    result['content_sha256']=canonical_sha(result)
    return result


def validate_material_group_contract(contract,samples,*,model=None):
    if (not isinstance(contract,dict) or contract.get('schema_version')!=1 or contract.get('mode')!='source_material_group_slots'
            or contract.get('content_sha256')!=canonical_sha({k:v for k,v in contract.items() if k!='content_sha256'})):
        raise ConversionError('Material-group contract identity changed')
    groups=material_group_table({'m_materialGroups':[dict(m_name=r['name'],m_materials=r['materials']) for r in contract['groups']]})
    if groups!=contract['groups'] or material_group_track(groups,samples)!=contract['track']:
        raise ConversionError('Material-group recorded selection changed')
    base=contract['base_materials']
    if (not isinstance(base,list) or not 1<=len(base)<=64 or base!=sorted(set(base))
            or any(safe_resource_path(name)!=name or not name.endswith('.vmat_c') for name in base)):
        raise ConversionError('Material-group base material inventory changed')
    if model is not None:
        if sha(model)!=contract['model_sha256']:raise ConversionError('Material-group model identity changed')
        if set(used_materials(read_glb(model)))!=set(base):raise ConversionError('Material-group original model coverage changed')
    bundle=contract.get('bundle')
    selected={target for row in contract['track']['selections'] for name,target in row['replacements'].items() if name in base}-set(base)
    if len(set(base)|selected)>64 or bool(bundle)!=bool(selected):raise ConversionError('Material-group selected bundle coverage differs')
    if bundle is not None:
        if bundle.get('materials')!=sorted(selected):raise ConversionError('Material-group bundle selected resources differ')
        if sha(bundle['path'])!=bundle['sha256']:raise ConversionError('Material-group bundle identity changed')
    return contract


def material_union_document(document,contract):
    """A planning-only document; never rewrite the immutable model or its rig."""
    result=copy.deepcopy(document);bundle=contract.get('bundle')
    if not bundle:return result
    if sha(bundle['path'])!=bundle['sha256']:raise ConversionError('Material-group bundle identity changed')
    materials=read_glb(bundle['path'])['materials'];start=len(result.get('materials',[]))
    result.setdefault('materials',[]).extend(materials)
    result.setdefault('meshes',[]).append(dict(primitives=[dict(material=start+index) for index in range(len(materials))]))
    return result


def material_group_summary(contract):
    return dict(status='applied_source_slots' if contract['track']['status']=='resolved_source_slots' else 'reference_slots',
        basis=contract['track']['status'],contract_sha256=contract['content_sha256'],selections=contract['track']['selections'],
        variant_materials=[] if contract['bundle'] is None else contract['bundle']['materials'])


def validate_material_group_consent(contract,*,poses_requested,partial):
    if poses_requested and contract['track']['status']=='not_recorded' and not partial:
        raise ValueError('Animated authored material groups lack recorded selection; explicitly enable --partial for a base-material reference')
