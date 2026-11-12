"""Asset-free actual mesh/normals regression, build and independent reopen.

Blender --background --python this_file -- <fresh-dir> [--reopen].
The repeated-index source triangles must never reach Blender's custom normals.
"""
from pathlib import Path
import sys,json
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_flashbang_sphere import build_mesh
args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]);reopen='--reopen'in args
positions=np.array([[0,0,0],[2,0,0],[0,2,0],[0,0,2]],dtype=np.float32)
uv=np.array([[0,0],[1,0],[0,1],[1,1]],dtype=np.float32)
normals=np.array([[0,0,1]]*4,dtype=np.float32)
faces=np.array([[0,1,2],[0,0,1],[2,2,2],[0,3,1]],dtype=np.uint32)
if not reopen:
    out.mkdir(parents=True,exist_ok=False);bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh=build_mesh('source geometry',dict(positions=positions,uv=uv,normals=normals,indices=faces),.0254)
    obj=bpy.data.objects.new(mesh.name,mesh);bpy.context.scene.collection.objects.link(obj)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'geometry.blend'))
else:
    bpy.ops.wm.open_mainfile(filepath=str(out/'geometry.blend'),load_ui=False,use_scripts=False)
    mesh=bpy.data.objects['source geometry'].data
assert [list(p.vertices)for p in mesh.polygons]==[[0,1,2],[0,3,1]]
assert mesh['agr_source_triangles']==4 and mesh['agr_repeated_index_triangles_removed']==2
assert np.max(abs(np.array([v.co[:]for v in mesh.vertices])-positions*.0254))<1e-7
for loop in mesh.loops:
    assert np.max(abs(np.array(mesh.uv_layers.active.data[loop.index].uv)-uv[loop.vertex_index]*[1,-1]-[0,1]))<1e-7
    assert np.dot(mesh.corner_normals[loop.index].vector,normals[loop.vertex_index])>.99999
result=dict(status='PASS',phase='reopen'if reopen else'build',visible_triangles=2,removed_repeated_index_triangles=2,loop_normals=6)
(out/(result['phase']+'-result.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result))
