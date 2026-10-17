// Bounded static world-node research exporter. Does not rewrite game files.
// Reuses exact VRF material/mesh conversion through a pinned private API adapter.
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using SharpGLTF.Schema2;
using ValveKeyValue;
using ValveResourceFormat.IO;
using ValveResourceFormat.Blocks;
using ValveResourceFormat.Serialization.KeyValues;
using VModel = ValveResourceFormat.ResourceTypes.Model;
using VMesh = ValveResourceFormat.ResourceTypes.Mesh;
using VMaterial = ValveResourceFormat.ResourceTypes.Material;
using VWorld = ValveResourceFormat.ResourceTypes.World;
using VWorldNode = ValveResourceFormat.ResourceTypes.WorldNode;

if(args.Length==1 && args[0]=="--self-test") { WorldNodeLayout.SelfTest(); return; }
if(args.Length!=1) throw new ArgumentException("config.json or --self-test");

// Inlined from the 20.0 ModelLodInfo helper; VRF 20.1 removed the class.
static bool IsInLowestSetLevel(long lodGroupMask,long combinedMask)
    => lodGroupMask == 0 || (lodGroupMask & (1L << (combinedMask == 0 ? 0 : BitOperations.TrailingZeroCount((ulong)combinedMask)))) != 0;
using var configFile=JsonDocument.Parse(File.ReadAllText(args[0]));
var config=configFile.RootElement;
var validateOnly=config.TryGetProperty("validate_only",out var validation) && validation.GetBoolean();
var operation=config.TryGetProperty("operation",out var operationValue)?operationValue.GetString():"export_region";
if(operation is not ("export_region" or "audit_resources"))throw new ArgumentException("Unsupported regional helper operation");
var assemblyHash=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(typeof(GltfModelExporter).Assembly.Location))).ToLowerInvariant();
if(assemblyHash!=config.GetProperty("vrf_sha256").GetString())throw new Exception("VRF private API assembly identity mismatch");
var output=Path.GetFullPath(config.GetProperty("output").GetString()!);
if(Directory.Exists(output)) throw new Exception("Output must be a new directory");
Directory.CreateDirectory(output);
using var loader=new ArchiveLoader(config.GetProperty("archives").EnumerateArray().Select(x=>x.GetString()!));
if(operation=="audit_resources")
{
    var resources=config.GetProperty("resources").EnumerateArray().Select(x=>x.GetString()!).ToArray();
    if(resources.Length is <1 or >100000)throw new ArgumentException("Resource audit bound exceeded");
    foreach(var name in resources)
        if(loader.FindFile(name).PackageEntry==null)throw new FileNotFoundException("Missing audited resource",name);
    File.WriteAllText(Path.Combine(output,"resource-report.json"),JsonSerializer.Serialize(loader.Payloads,new JsonSerializerOptions{WriteIndented=true}));
    Console.WriteLine($"Audited {loader.Payloads.Count} resources from explicit archives");
    return;
}
var bounds=config.GetProperty("bounds").EnumerateArray().Select(v=>v.EnumerateArray().Select(x=>x.GetSingle()).ToArray()).ToArray();
if(bounds.Length!=3 || bounds.Any(v=>v.Length!=2 || !float.IsFinite(v[0]) || !float.IsFinite(v[1]) || v[0]>=v[1])) throw new Exception("Invalid bounds");
using var worldResource=loader.LoadFileCompiled(config.GetProperty("world").GetString()!) ?? throw new Exception("Missing world");
var world=(VWorld)worldResource.DataBlock!;
var scale=world.GetLightmapUvScale();
if(!float.IsFinite(scale.X)||!float.IsFinite(scale.Y)||scale.X<=0||scale.Y<=0)
    throw new Exception("Invalid world lightmap UV scale");
var exporter=new GltfModelExporter(loader){ExportAnimations=false,ExportMaterials=true,ExportExtras=true,ProgressReporter=new Progress<string>(static _=>{})};
var type=typeof(GltfModelExporter);
object? Call(string name,params object?[] values) => (type.GetMethod(name,BindingFlags.NonPublic|BindingFlags.Static|BindingFlags.Instance) ?? throw new Exception("Missing pinned API: "+name)).Invoke(exporter,values);
void Field(string name,object value) => (type.GetField(name,BindingFlags.NonPublic|BindingFlags.Instance) ?? throw new Exception(name)).SetValue(exporter,value);
Field("DstDir",output); Field("LightmapUvScale",scale);
object?[] modelArgs=["AGR current static region",null];
var gltf=(ModelRoot)Call("CreateModelRoot",modelArgs)!;
var scene=(Scene)modelArgs[1]!;
var rows=new List<object>(); var validatedNodes=new List<string>(); int inspected=0, outside=0, selected=0;
var materials=new Dictionary<string,ValveResourceFormat.Resource>();
var positionCache=new Dictionary<(VBIB,int),Vector3[]>();
var accessorCache=new Dictionary<VBIB,object>();

void Draw(VMesh mesh,KVObject draw,Matrix4x4 transform,Vector4 tint,string modelName,int drawIndex,string placement)
{
    inspected++;
    var vbib=mesh.VBIB;
    var positions=new List<Vector3[]>();
    foreach(var binding in draw.GetArray("m_vertexBuffers"))
    {
        var index=binding.GetInt32Property("m_hBuffer");
        var buffer=vbib.VertexBuffers[index];
        foreach(var field in buffer.InputLayoutFields.Where(f=>f.SemanticName=="POSITION"))
        {
            if(!positionCache.TryGetValue((vbib,index),out var values))
                positionCache[(vbib,index)]=values=VBIB.GetVector3AttributeArray(buffer,field);
            positions.Add(values);
        }
    }
    if(positions.Count!=1) throw new Exception("Ambiguous position stream");
    var ib=draw.GetSubCollection("m_indexBuffer").GetInt32Property("m_hBuffer");
    var indices=GltfModelExporter.ReadIndices(vbib.IndexBuffers[ib],draw.GetInt32Property("m_nStartIndex"),draw.GetInt32Property("m_nIndexCount"),draw.GetInt32Property("m_nBaseVertex"));
    if(indices.Length==0 || indices.Any(i=>i<0 || i>=positions[0].Length)) throw new Exception("Invalid draw indices");
    var min=new Vector3(float.PositiveInfinity);var max=new Vector3(float.NegativeInfinity);
    foreach(var i in indices)
    {
        var v=Vector3.Transform(positions[0][i],transform);
        if(!float.IsFinite(v.X)||!float.IsFinite(v.Y)||!float.IsFinite(v.Z)) throw new Exception("Nonfinite position");
        min=Vector3.Min(min,v);max=Vector3.Max(max,v);
    }
    if(Enumerable.Range(0,3).Any(i=>max[i]<bounds[i][0] || min[i]>bounds[i][1])) {outside++;return;}
    if(++selected>10000) throw new Exception("Region draw bound exceeded");
    var materialName=draw.GetStringProperty("m_material") ?? draw.GetStringProperty("m_pMaterial") ?? throw new Exception("Draw call without material");
    if(!materials.TryGetValue(materialName,out var materialResource))
        materials[materialName]=materialResource=loader.LoadFileCompiled(materialName) ?? throw new Exception(materialName);
    var material=(VMaterial)materialResource.DataBlock!;
    int channel=0;var channels=new List<int>();
    foreach(var binding in draw.GetArray("m_vertexBuffers"))
    foreach(var field in vbib.VertexBuffers[binding.GetInt32Property("m_hBuffer")].InputLayoutFields.OrderBy(f=>f.SemanticIndex).ThenBy(f=>f.Offset))
    {
        if(field.SemanticName=="TEXCOORD" && VBIB.GetFormatInfo(field).ElementCount==2)
        {
            if(VMaterial.FindD3DInputSignatureElement(material.InputSignature,field.SemanticName,field.SemanticIndex).Name is "vLightmapUV" or "vLightmapUVW") channels.Add(channel);
            channel++;
        }
    }
    var baked=VMesh.HasBakedLightingFromLightMap(draw);
    var qualified=baked && channels.Count==1;
    if(!accessorCache.TryGetValue(vbib,out var accessors))
        accessorCache[vbib]=accessors=Call("CreateVertexBufferAccessors",gltf,vbib,0,null)!;
    var name=$"agr_draw_{selected:00000}";
    var target=gltf.CreateMesh(name);
    var primitive=(MeshPrimitive)Call("CreateMeshFromDrawCall",draw,target,vbib,accessors,gltf,null,tint)!;
    if(primitive.Material==null) throw new Exception("Material export missing");
    var metadata=new JsonObject { ["model"]=modelName,["draw"]=drawIndex,["material"]=materialName,
        ["shader"]=material.ShaderName,["baked"]=baked,["qualified"]=qualified,
        ["uv_channel"]=qualified?channels[0]:-1,["uv_scale_baked"]=qualified,["placement"]=placement };
    var placedNode=scene.CreateNode(name).WithMesh(target);
    placedNode.Extras=new JsonObject{["agr_source_draw"]=metadata};
    placedNode.WorldMatrix=(Matrix4x4)Call("GetPlacementTransform",transform)!;
    rows.Add(new {name,model=modelName,draw=drawIndex,material=materialName,qualified,uv_channel=qualified?channels[0]:-1,
        bounds_min=new[]{min.X,min.Y,min.Z},bounds_max=new[]{max.X,max.Y,max.Z},indices=indices.Length});
}

foreach(var worldNodeName in world.GetWorldNodeNames())
{
    using var resource=loader.LoadFile(worldNodeName+".vwnod_c") ?? throw new Exception("Missing worldnode");
    var node=(VWorldNode)resource.DataBlock!;
    WorldNodeLayout.Validate(node.Data,worldNodeName);
    validatedNodes.Add(worldNodeName);
    if(validateOnly)continue;
    int objectIndex=0;
    foreach(var obj in node.SceneObjects)
    {
        var modelName=obj.GetStringProperty("m_renderableModel"); if(string.IsNullOrEmpty(modelName)) continue;
        using var modelResource=loader.LoadFileCompiled(modelName) ?? throw new Exception(modelName);
        var model=(VModel)modelResource.DataBlock!;
        var meshes=model.GetEmbeddedMeshesForLod(model.LodInfo.LowestLevel).ToList();
        if(model.GetReferenceMeshNamesForLod(model.LodInfo.LowestLevel).Any()) throw new Exception("Referenced worldnode meshes not supported yet");
        var tint=obj.GetSubCollection("m_vTintColor").ToVector4();if(tint==Vector4.Zero)tint=Vector4.One;
        var transform=obj.GetArray("m_vTransform").ToMatrix4x4();
        var drawIndex=0;
        foreach(var mesh in meshes) foreach(var o in mesh.Mesh.Data.GetArray("m_sceneObjects")) foreach(var draw in o.GetArray("m_drawCalls"))
            Draw(mesh.Mesh,draw,transform,tint,modelName,drawIndex++,$"{worldNodeName}/scene/{objectIndex}");
        positionCache.Clear();accessorCache.Clear();objectIndex++;
    }
    objectIndex=0;
    foreach(var obj in node.AggregateSceneObjects)
    {
        var modelName=obj.GetStringProperty("m_renderableModel");if(string.IsNullOrEmpty(modelName))continue;
        using var modelResource=loader.LoadFileCompiled(modelName) ?? throw new Exception(modelName);
        var model=(VModel)modelResource.DataBlock!;var meshes=model.GetEmbeddedMeshes().ToList();
        if(meshes.Count!=1) throw new Exception("Expected one embedded aggregate mesh");
        var mesh=meshes[0].Mesh;
        var draws=mesh.Data.GetArray("m_sceneObjects").SelectMany(o=>o.GetArray("m_drawCalls")).ToList();
        var fragments=obj.GetArray("m_aggregateMeshes");var transforms=obj.GetArray("m_fragmentTransforms");int transformIndex=0;
        var masks=new Dictionary<int,uint>();
        foreach(var f in fragments){var setup=f.GetInt32Property("m_nLODSetupIndex",-1);masks.TryGetValue(setup,out var mask);masks[setup]=mask|f.GetUInt32Property("m_nLODGroupMask");}
        foreach(var f in fragments)
        {
            var transform=f.GetBooleanProperty("m_bHasTransform")?transforms[transformIndex++].ToMatrix4x4():Matrix4x4.Identity;
            if(transform.GetDeterminant()==0)continue;
            if(!IsInLowestSetLevel(f.GetUInt32Property("m_nLODGroupMask"),masks[f.GetInt32Property("m_nLODSetupIndex",-1)]))continue;
            var index=f.GetInt32Property("m_nDrawCallIndex");if(index<0||index>=draws.Count)throw new Exception("Aggregate draw outside mesh");
            var tint=f.ContainsKey("m_vTintColor")?new Vector4(f.GetSubCollection("m_vTintColor").ToVector3()/255f,1):Vector4.One;
            Draw(mesh,draws[index],transform,tint,modelName,index,$"{worldNodeName}/aggregate/{objectIndex}");
        }
        if(transformIndex!=transforms.Count)throw new Exception("Unconsumed fragment transform");
        positionCache.Clear();accessorCache.Clear();objectIndex++;
        if(objectIndex%100==0)Console.WriteLine($"Scanned {objectIndex} aggregates; selected {selected} draws");
    }
}
if(validateOnly)
{
    if(validatedNodes.Count==0)throw new Exception("World has no nodes");
    File.WriteAllText(Path.Combine(output,"layout-report.json"),JsonSerializer.Serialize(new {
        schema_version=1,status="qualified_layout",validated_nodes=validatedNodes,vrf_sha256=assemblyHash,inputs=loader.Payloads,
        scope="World-node layout only; no geometry, texture, shader, saved-scene or visual validation"},
        new JsonSerializerOptions{WriteIndented=true}));
    Console.WriteLine($"Qualified layout of {validatedNodes.Count} world nodes; no geometry exported");
    return;
}
if(selected==0)throw new Exception("Empty region");
// VRF's internal wait skips already-completed tasks, including faulted ones.
// Explicitly await every texture task so a missing native decoder cannot look successful.
var textureTasks=(IEnumerable<Task>)type.GetField("TextureExportingTasks",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(exporter)!;
Task.WhenAll(textureTasks).GetAwaiter().GetResult();
Call("WriteModelFile",gltf,Path.Combine(output,"region.gltf"),null);
File.WriteAllText(Path.Combine(output,"region-report.json"),JsonSerializer.Serialize(new {schema_version=1,inspected,outside,selected,rows,validated_nodes=validatedNodes,loaded=loader.Loaded.Order().ToArray(),inputs=loader.Payloads,
    scope="World-node static geometry only; entity-lump models/lights and clutter not yet integrated",uv_scale=new[]{scale.X,scale.Y}},new JsonSerializerOptions{WriteIndented=true}));
foreach(var material in materials.Values)material.Dispose();
Console.WriteLine($"Exported {selected} static draws from {inspected} inspected");

