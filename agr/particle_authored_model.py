"""Qualified static mask meshes and authored VMAT slots for model particles.

This admits the observed model renderer, not activation/lifetime of its parent
system. Other model selectors/material variables remain separately unsupported.
"""
import json
import math
import subprocess
from pathlib import Path
import numpy as np
from .asset_converter import compiled,sha,read_glb,ConversionError
from .installed_effects import UnsupportedEffect
from .particle_model_bindings import authored_primitive_bindings,authored_material_slot_bytes
from .particle_model_selection import authored_bodygroup_selection
from .source_hero_lighting_package import canonical_sha

POLICY='agr_particle_authored_static_model_v1'
MODEL='models/props_gameplay/mask/halloween_mask.vmdl'
SOURCE_SHA='7f5a00348bc7805463d16ba601a550eff50f53a3c09aa0f34c2014de1a034c18'
RENDERER_SHA='8de0dff990e17119e82bcfd1b9300fb0d97c1b47738a345157b8f3053408f94b'
PARTICLES_SHA='46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f'
SCENE_SHA='1ddcd13dd51652bd6d3e183172be9d46df98bc6a266a0e804d09369ba6096d9a'
FIELD_POLICY='agr_model_material_fields_v1'

def require(value,message):
    if not value:raise UnsupportedEffect(message)

def is_authored_renderer(renderer):
    try:return canonical_sha(renderer)==RENDERER_SHA
    except (ValueError,TypeError):return False

def _static_geometry(path,inventory,source_sha):
    require(isinstance(inventory,dict),'Authored model inventory missing')
    doc=read_glb(path)
    require(not any(doc.get(k)for k in ('skins','animations','extensionsRequired')),
            'Authored model must be static and unskinned')
    nodes=doc.get('nodes',[])
    require(len(nodes)==6 and len(doc.get('scenes',[]))==1
            and sorted(doc['scenes'][0].get('nodes',[]))==list(range(len(nodes))),
            'Authored mask scene/node coverage differs')
    for node in nodes:
        matrix=np.asarray(node.get('matrix',np.eye(4).ravel()),dtype=float)
        require(not any(k in node for k in ('children','skin','translation','rotation','scale','weights'))
                and matrix.shape==(16,) and np.isfinite(matrix).all()
                and np.allclose(matrix,np.eye(4).ravel(),atol=2e-6,rtol=0),
                'Authored mask node transform is not an inert source frame')
    bindings=authored_primitive_bindings(path,inventory,resource=MODEL,
        source_sha256=source_sha,geometry_sha256=sha(path))
    require(len(bindings['meshes'])==6 and all(call['tint']==[1,1,1,1]
        for row in bindings['meshes']for call in row['primitives']),
        'Authored mask draw-call tint or mesh coverage differs')
    return bindings

def validate_authored_model(renderer,descriptor):
    require(is_authored_renderer(renderer) and isinstance(descriptor,dict)
        and descriptor.get('policy')==POLICY and type(descriptor.get('schema_version'))is int and descriptor['schema_version']==1
        and descriptor.get('resource')==MODEL and descriptor.get('source_sha256')==SOURCE_SHA,
        'Missing qualified authored mask model')
    require(isinstance(descriptor.get('directory'),str) and isinstance(descriptor.get('inventory'),dict)
        and isinstance(descriptor.get('bindings'),dict),'Authored mask asset metadata missing')
    require(descriptor.get('native_selector')==dict(particles_sha256=PARTICLES_SHA,
        renderer_sha256=RENDERER_SHA,body_group=0,submodel_field=26,subtype=0,scenesystem_sha256=SCENE_SHA),
        'Authored mask selector provenance differs')
    directory=Path(descriptor['directory']);path=directory/'model.glb'
    require(path.is_file() and descriptor.get('geometry_file')=='model.glb'
        and sha(path)==descriptor.get('geometry_sha256'),'Authored mask geometry identity changed')
    bindings=_static_geometry(path,descriptor['inventory'],SOURCE_SHA)
    require(bindings==descriptor.get('bindings'),'Authored mask primitive bindings changed')
    initial=descriptor.get('default_mesh_group_mask')
    require(type(initial)is int and initial==1,'Authored mask default bodygroup changed')
    for choice in range(6):
        selected=authored_bodygroup_selection(descriptor['inventory'],bindings,
            initial_mask=initial,body_group=0,submodel=choice)
        require(len(selected['source_mesh_indices'])==1,'Authored mask choice is ambiguous')
    slots=directory/'slots.glb'
    require(descriptor.get('slot_file')=='slots.glb' and slots.is_file()
        and sha(slots)==descriptor.get('slot_sha256')
        and slots.read_bytes()==authored_material_slot_bytes(path,bindings),
        'Authored mask slot geometry changed')
    bundle=descriptor.get('material_bundle',{});material_path=Path(bundle.get('path',''))
    require(material_path.is_file() and 20<=material_path.stat().st_size<=256*1024**2
        and sha(material_path)==bundle.get('sha256'),'Authored mask material bundle changed')
    document=read_glb(material_path)
    materials={compiled(call['material'])for row in bindings['meshes']for call in row['primitives']}
    actual=[compiled(m.get('extras',{}).get('vmat',{}).get('Name',''))for m in document.get('materials',[])]
    require(not any(document.get(k)for k in ('meshes','nodes','skins','animations'))
        and len(actual)==len(materials) and set(actual)==materials==set(bundle.get('materials',[])),
        'Authored mask material-only resource coverage differs')
    return descriptor

def resolved_sample_selections(descriptor,samples,simulation):
    require(simulation.get('model_material_fields_policy')==FIELD_POLICY,
        'Authored model requires captured subtype selector fields')
    require(isinstance(samples,list) and samples,'Authored model requires nonempty sampled particles')
    result=[]
    for tick,_,particle in samples:
        color=particle.get('color')
        require(isinstance(color,list) and len(color)==3 and all(type(v)in(int,float)and v==1 for v in color)
            and all(type(particle.get(k))in(int,float)and particle[k]==1 for k in ('alpha','alpha_alternate')),
            'Authored mask tint/alpha response is not qualified')
        selector=particle.get('scratch_float1')
        require(type(selector)in(int,float) and math.isfinite(selector),
            'Missing finite authored mask selector field26')
        result.append(authored_bodygroup_selection(descriptor['inventory'],descriptor['bindings'],
            initial_mask=descriptor['default_mesh_group_mask'],body_group=0,submodel=selector))
    return result

def prepare_authored_model(adapter,renderer,provider,probe,dotnet=None,*,material_command=None,material_environment=None):
    from .particle_runtime import particle_command,particle_environment
    from .model_material_groups import _bundle
    require(is_authored_renderer(renderer),'Unqualified authored model renderer')
    module=Path(provider.install_path)/'game/bin/win64/particles.dll'
    require(module.is_file() and sha(module)==PARTICLES_SHA,
        'Authored mask selectors need the qualified installed particle module')
    scene_module=Path(provider.install_path)/'game/bin/win64/scenesystem.dll'
    require(scene_module.is_file() and sha(scene_module)==SCENE_SHA, 'Authored mask bodygroups need the qualified scene module')
    source=provider.resolve(compiled(MODEL));require(source.sha256==SOURCE_SHA,'Authored mask source changed')
    decoder=adapter.decoder;probe=Path(probe).resolve();pending=[compiled(MODEL)];inputs={}
    while pending:
        resource=pending.pop()
        if resource in inputs:continue
        require(len(inputs)<256,'Authored model dependency bound exceeded')
        item=provider.resolve(resource);inputs[resource]=item.sha256
        pending.extend(compiled(n)for n in item.dependencies)
    identity=dict(policy=POLICY,adapter_sha256=sha(Path(__file__)),
        binding_adapter_sha256=sha(Path(__file__).with_name('particle_model_bindings.py')),
        probe_sha256=sha(probe),parser_sha256=sha(probe.with_name('ValveResourceFormat.dll')),
        particles_sha256=PARTICLES_SHA)
    command=particle_command(probe,dotnet);env=particle_environment(probe,dotnet)
    def build(temp):
        output=temp/'model';gameinfo=str(provider.install_path/'game/citadel/gameinfo.gi')
        result=subprocess.run(command+['--particle-authored-model-v1',gameinfo,MODEL,str(output)],
            env=env,capture_output=True,text=True,timeout=180)
        (temp/'model.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode:raise ConversionError('Authored particle model export failed: '+result.stderr[-1500:])
        metadata=json.loads((output/'model.json').read_text(encoding='utf-8'))
        require(metadata.get('policy')==POLICY and metadata.get('resource')==MODEL and metadata.get('source_sha256')==SOURCE_SHA
            and metadata.get('bones')==0 and metadata.get('animations')==[]
            and metadata.get('body_groups')==1 and metadata.get('material_exported')is False,
            'Authored mask export metadata differs')
        result=subprocess.run(command+['--inventory-particle-model-materials',gameinfo,MODEL],
            env=env,capture_output=True,text=True,timeout=120)
        (temp/'inventory.log').write_text(result.stderr,encoding='utf-8')
        if result.returncode:raise ConversionError('Select a particle helper with authored material inventory support')
        inventory=json.loads(result.stdout);bindings=_static_geometry(output/'model.glb',inventory,SOURCE_SHA)
        (output/'inventory.json').write_text(json.dumps(inventory),encoding='utf-8')
        (output/'slots.glb').write_bytes(authored_material_slot_bytes(output/'model.glb',bindings))
    directory=decoder._entry('particle_authored_static_model_v1',inputs,identity,build)/'model'
    inventory=json.loads((directory/'inventory.json').read_text(encoding='utf-8'))
    bindings=_static_geometry(directory/'model.glb',inventory,SOURCE_SHA)
    materials={compiled(call['material'])for row in bindings['meshes']for call in row['primitives']}
    bundle=_bundle(materials,provider,decoder,command=material_command,environment=material_environment)
    data=decoder.dump(MODEL,provider)
    descriptor=dict(schema_version=1,policy=POLICY,resource=MODEL,source_sha256=SOURCE_SHA,
        directory=str(directory),geometry_file='model.glb',geometry_sha256=sha(directory/'model.glb'),
        slot_file='slots.glb',slot_sha256=sha(directory/'slots.glb'),inventory=inventory,bindings=bindings,
        default_mesh_group_mask=data.get('m_nDefaultMeshGroupMask'),material_bundle=bundle,
        native_selector=dict(particles_sha256=PARTICLES_SHA,renderer_sha256=RENDERER_SHA,
            body_group=0,submodel_field=26,subtype=0,scenesystem_sha256=SCENE_SHA),converter_identity=identity)
    validate_authored_model(renderer,descriptor)
    return descriptor,{}
