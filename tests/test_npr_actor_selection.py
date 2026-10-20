import copy
from types import SimpleNamespace
import pytest
from agr import blender_npr_selection as selection
from agr.asset_converter import sha
from agr.source_npr_package import build_npr_material_package,validate_npr_material_package
from test_source_npr_package import inputs


class Material(dict):
    __hash__=object.__hash__
    def __init__(self,name):
        super().__init__(vmat=dict(Name=name,ShaderName='pbr.vfx',IntParams=dict(F_USE_NPR_LIGHTING=1)))
        self.name=name


class Mesh(dict):
    __hash__=object.__hash__
    type='MESH'
    def __init__(self,name,materials):
        super().__init__();self.name=name
        self.material_slots=[SimpleNamespace(material=m) for m in materials]


def test_combined_79_materials_stay_in_two_validated_actor_packages(tmp_path):
    plan,decoded,provider,_=inputs(tmp_path)
    entities=[];packages={};roots=[];models={};selections=[]
    for identity,count in [('a',40),('b',39)]:
        source=tmp_path/(identity+'.glb');source.write_bytes(identity.encode());digest=sha(source)
        entity=dict(id=identity,model_file=str(source));entities.append(entity)
        selections.append(dict(entity_id=identity,model_sha256=digest))
        local_plan=copy.deepcopy(plan);local_decoded=copy.deepcopy(decoded)
        local_plan['materials']=[];local_decoded['materials']=[];materials=[]
        for i in range(count):
            name=f'models/{identity}/material_{i}.vmat_c'
            for target,original in [(local_plan,plan),(local_decoded,decoded)]:
                row=copy.deepcopy(original['materials'][0]);row['material']=name;target['materials'].append(row)
            materials.append(Material(name))
        package=build_npr_material_package(local_plan,local_decoded,provider,allow_partial=True)
        assert len(validate_npr_material_package(package))==count
        models[digest]=package
        root=SimpleNamespace(children_recursive=[Mesh(identity,materials)],get=lambda key,value=identity:value)
        roots.append(root)
    scene=dict(schema_version=1,status='prepared_requires_blender_validation',allow_partial=True,
               entities=selections,models=models)
    actors=selection.select_npr_actor_materials(scene,entities,roots,manifest_dir=tmp_path)
    assert [len(r['materials']) for r in actors]==[40,39]
    assert [r['package'] for r in actors]==list(models.values())
    assert sum(len(r['materials']) for r in actors)==79
    # Every per-model validator retains its64-material guard; no global package
    # is manufactured or accepted by raising that native budget.
    oversized=copy.deepcopy(models[next(iter(models))])
    oversized['materials']*=2
    with pytest.raises(ValueError):validate_npr_material_package(oversized)
