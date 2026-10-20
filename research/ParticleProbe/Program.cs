using System.Numerics;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Particles;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Buffers.Binary;
if(args.Length==1 && args[0]=="--capabilities-v1") {
 Console.WriteLine(JsonSerializer.Serialize(new { schema_version=1, commands=new[] {
  "physics-json-v1", "material-bundle-v1", "decompile-cloth", "inventory-cloth-physics",
  "inventory-animation-clip-events", "inventory-model-hitboxes" } }));
 return;
}
if(args.Length==4 && args[0]=="--material-bundle-v1") { MaterialBundleCommand.Run(args[1],args[2],args[3]);return; }
if(args.Length==4 && args[0]=="--physics-json-v1") { FePhysicsJsonCommand.Run(args[1],args[2],args[3]);return; }
if(args.Length==4 && args[0]=="--decompile-cloth") { ClothExtractCommand.Run(args[1],args[2],args[3]);return; }
if(args.Length==4 && args[0]=="--inventory-cloth-physics") { ClothExtractCommand.Inventory(args[1],args[2],args[3]);return; }
if(args.Length==4 && args[0]=="--inventory-animation-clip-events") { ClothExtractCommand.InventoryClipEvents(args[1],args[2],args[3]);return; }
if(args.Length==4 && args[0]=="--inventory-model-hitboxes") { ModelHitboxInventory.Run(args[1],args[2],args[3]);return; }
if(args.Length>0 && args[0]=="--model-sampling-oracle") { ModelSamplingOracle.Check(args[1],args[2]);return; }
if(args.Length>0 && args[0]=="--ramp-spline-oracle") { RampSplineOracle.Check(args[1],args[2]);return; }
if(args.Length>0 && args[0]=="--invalid-transform-oracle") { TransformOracle.Check(args[1]);return; }
if(args.Length>0 && args[0]=="--control-point-oracle") { ControlPointAudit.Check(args[1]);return; }
if(args.Length>0 && args[0]=="--replay-lifecycle-oracle") { ReplayLifecycle.Check(args[1]);return; }
if(args.Length>0 && args[0]=="--set-child-control-oracle") { SetChildControlOracle.Check(args[1],args[2]);return; }
if(args.Length>0 && args[0]=="--per-child-control-oracle") { SetPerChildControlOracle.Check(args[1],args[2]);return; }
if(args.Length==3 && args[0]=="--per-child-replay-study") { PerChildReplayStudy.Run(args[1],args[2]);return; }
if(typeof(ParticleSystemSimulation).Assembly.GetType("ValveResourceFormat.Particles.Operators.RampScalarSpline")==null)
 throw new InvalidOperationException("Apply research/patches/vrf-ramp-scalar-spline.patch to the pinned VRF checkout before building ParticleProbe.");
TransformOracle.Check();
var requestBytes=File.ReadAllBytes(args[0]);
var requestSha256=Convert.ToHexStringLower(SHA256.HashData(requestBytes));
using var document=JsonDocument.Parse(requestBytes);var config=document.RootElement;
var log=new CaptureLog();
uint? seed=config.TryGetProperty("seed",out var seedInput)?seedInput.GetUInt32():null;
using var loader=new GameFileLoader(null,config.GetProperty("gameinfo").GetString(),log);
int tickRate=config.GetProperty("tick_rate").GetInt32(),start=config.GetProperty("start_tick").GetInt32(),end=config.GetProperty("end_tick").GetInt32();
if(tickRate<=0||start<0||end<start||end==int.MaxValue)throw new Exception("Invalid simulation output range or tick rate");
float[] V(Vector3 v)=>new[]{v.X,v.Y,v.Z};
var output=new List<object>();
foreach(var instance in config.GetProperty("instances").EnumerateArray()) {
 var id=instance.GetProperty("id").GetString();var name=instance.GetProperty("resource").GetString()!;int birth=instance.GetProperty("created_tick").GetInt32();
 var controls=(instance.TryGetProperty("control_points",out var ownControls)?ownControls:config.GetProperty("control_points")).EnumerateArray().ToDictionary(x=>x.GetProperty("tick").GetInt32(),x=>x);
 var replayEvents=ReplayEventTimeline.Parse(instance,birth,end);
 if(birth<0||birth>end)throw new Exception("Instance birth must precede output end and be nonnegative");
 // Pre-roll must use an explicit complete history. Never hold the first shot
 // transform backwards or restart an older effect at the output boundary.
 int? modelCount=null;string? extraLayout=null;
 for(int tick=birth;tick<=end;tick++) {
  if(!controls.TryGetValue(tick,out var control))throw new Exception($"{id}: missing control-point history at tick {tick}");
  int boneCount=control.TryGetProperty("model_bones",out var modelBones)?modelBones.GetArrayLength():0;
  string layout=control.TryGetProperty("extra_control_points",out var extraPoints)?string.Join(",",extraPoints.EnumerateArray().Select(x=>x.GetProperty("index").GetInt32()).Order()):"";
  if(modelCount.HasValue&&(modelCount!=boneCount||extraLayout!=layout))throw new Exception("Incomplete/changing model or extra control-point history");
  modelCount=boneCount;extraLayout=layout;
  var position=control.GetProperty("position").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
  var rotation=control.GetProperty("rotation").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
  if(position.Length!=3||rotation.Length!=4||position.Any(v=>!float.IsFinite(v))||rotation.Any(v=>!float.IsFinite(v))
      ||!float.IsFinite(rotation.Sum(v=>v*v))||rotation.Sum(v=>v*v)<1e-12f)
   throw new Exception($"{id}: invalid control-point transform at tick {tick}");
 }
 replayEvents.ValidateControls(controls,birth,end);
 log.Prefix=id!;
 using var resource=loader.LoadFileCompiled(name)??throw new Exception("Missing "+name);
 var sim=new ParticleSystemSimulation((ParticleSystem)resource.DataBlock!,loader,log);var frames=new List<object>();
 var seedBindings=seed.HasValue?SeedBinding.Apply(sim,seed.Value,id!):new List<SeedBinding>();
 var seedResets=new List<object>();
 bool destroyedImmediately=false;
 for(int tick=birth;tick<=end;tick++) {
  if(!destroyedImmediately) {
  var cp=controls[tick];var p=cp.GetProperty("position").EnumerateArray().Select(v=>v.GetSingle()).ToArray();var q=cp.GetProperty("rotation").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
  sim.MainControlPoint.Position=new Vector3(p[0],p[1],p[2]);sim.MainControlPoint.Rotation=Quaternion.Normalize(new Quaternion(q[0],q[1],q[2],q[3]));
  if(cp.TryGetProperty("model_bones",out var modelInput)) {
   var bones=modelInput.EnumerateArray().Select(value=>{
    var v=value.EnumerateArray().Select(x=>x.GetSingle()).ToArray();
    if(v.Length!=8||v.Any(x=>!float.IsFinite(x))||v[3]<=0||MathF.Abs(v.Skip(4).Sum(x=>x*x)-1)>2e-5f)
     throw new Exception("Invalid model bone pose");
    return new ModelBonePose(new(v[0],v[1],v[2]),new(v[4],v[5],v[6],v[7]),v[3]);
   }).ToArray();
   if(bones.Length==0||bones.Length>32767)throw new Exception("Invalid model bone count");
   sim.RenderState.ModelBoneProvider=index=>index==0?bones:throw new Exception("Only explicit CP0 model bones supplied");
  } else sim.RenderState.ModelBoneProvider=null;
  if(cp.TryGetProperty("extra_control_points",out var extras)) {
   var seen=new HashSet<int>();
   foreach(var extra in extras.EnumerateArray()) {
    var index=extra.GetProperty("index").GetInt32();
    var ep=extra.GetProperty("position").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
    var eq=extra.GetProperty("rotation").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
    if(index<1||index>63||!seen.Add(index)||ep.Length!=3||eq.Length!=4||ep.Any(x=>!float.IsFinite(x))
      ||eq.Any(x=>!float.IsFinite(x))||MathF.Abs(eq.Sum(x=>x*x)-1)>2e-5f)throw new Exception("Invalid extra control point");
    var target=sim.RenderState.GetControlPoint(index);target.Position=new(ep[0],ep[1],ep[2]);target.Rotation=new(eq[0],eq[1],eq[2],eq[3]);
   }
  }
  if(tick==birth) {
   ControlPointAudit.BeginInputs(sim,new[]{0}.Concat((extraLayout??"").Split(',',StringSplitOptions.RemoveEmptyEntries).Select(int.Parse)));
   sim.Start();
  }
  destroyedImmediately=replayEvents.DestroyAt(tick,sim);
  if(seed.HasValue&&replayEvents.StartsEndCapAt(tick))
   foreach(var binding in SeedBinding.RestoreEndCapSeeds(sim,seedBindings))seedResets.Add(new{tick,path=binding.Path,seed=binding.Value});
  // A tiny first step allows instantaneous emission at the recorded birth tick.
  float targetAge=(tick-birth)/(float)tickRate;float dt=MathF.Max(1e-6f,targetAge-sim.RenderState.Age);
  if(!destroyedImmediately)sim.Update(dt,tick/(float)tickRate);
  }
  foreach(var binding in seedBindings)binding.Verify();
  if(tick<start)continue;
  var systems=new List<object>();
  void Capture(ParticleSystemSimulation s,string path) {
   var particles=new List<object>();
   foreach(ref var x in s.Particles.Current)particles.Add(new{id=x.UniqueParticleId,position=V(x.Position),previous=V(x.PositionPrevious),velocity=V(x.Velocity),radius=x.Radius,alpha=x.Alpha,alpha_alternate=x.AlphaAlternate,color=V(x.Color),life=x.Lifetime,age=x.Age,creation_time=x.CreationTime,scratch_float0=x.ScratchFloat0,hitbox_index=x.HitboxIndex,hitbox_coordinates=V(x.HitboxOffsetPosition),trail=x.TrailLength,normal=V(x.Normal),rotation=V(x.Rotation),sequence=x.SequenceNumber,manual_frame=x.ManualAnimationFrame});
   systems.Add(new{name=s.Name,system_path=path,step=s.Particles.CurrentFrameTime,particles});
   for(int childIndex=0;childIndex<s.Children.Count;childIndex++)Capture(s.Children[childIndex],path+"/"+childIndex);
  }
  Capture(sim,"root");frames.Add(new{tick,systems});
 }
 output.Add(new{id,resource=name,birth,frames,model_bone_count=modelCount??0,control_point_indices=new[]{0}.Concat((extraLayout??"").Split(',',StringSplitOptions.RemoveEmptyEntries).Select(int.Parse)).ToArray(),materialized_control_point_indices=ControlPointAudit.Indices(sim),control_point_input_policy=ControlPointAudit.InputProvenance(sim,"ControlPointInputPolicy"),declared_control_point_inputs=ControlPointAudit.InputProvenance(sim,"DeclaredControlPointInputs"),authored_control_point_indices=ControlPointAudit.InputProvenance(sim,"AuthoredControlPointIndices"),unprovided_control_point_indices=ControlPointAudit.InputProvenance(sim,"UnprovidedControlPointIndices"),system_seeds=seedBindings.Select(b=>new{path=b.Path,resource=b.Resource,seed=b.Value}).ToArray(),system_seed_resets=seedResets,replay_event_policy=ReplayEventTimeline.Policy,literal_transforms_applied=replayEvents.LiteralReport,destroy_applied=replayEvents.DestroyReport,release_ticks=replayEvents.ReleaseTicks});
}
File.WriteAllText(args[1],JsonSerializer.Serialize(new{schema_version=1,backend="ValveResourceFormat 42743fb93030c552f834fabb30708dad84d0cbf5 + AGR RampScalarSpline v1 + PT_TYPE_INVALID identity v1 + experimental CreateOnModel bones v1",request_sha256=requestSha256,seed,seed_policy=seed.HasValue?"agr_substitute_sha256_v1":"upstream_random",tick_rate=tickRate,start_tick=start,end_tick=end,warnings=log.Messages,instances=output},new JsonSerializerOptions{WriteIndented=true}));
Console.WriteLine($"Simulated {output.Count} recorded instances; {log.Messages.Count} warnings");
if(log.Messages.Count>0)Environment.ExitCode=2;
// Version-bound instrumentation of the pinned simulator, not the game process.
// Set seeds before Start consumes random draws; fail if the private contract or
// lifecycle changes. These reproducible seeds are NOT recovered engine seeds.
class SeedBinding {
 public required string Path;public required string Resource;public required int Value;
 private object Random=null!;private PropertyInfo SeedProperty=null!;
 public void Verify(){if(SeedProperty.GetValue(Random) is not int actual||actual!=Value)throw new Exception("Unexpected particle reseed: "+Path);}
 // PlayEndCap explicitly rewinds each authored endcap subtree. Restore only
 // those pristine RNGs to their recorded AGR substitute seed, before Start or
 // any initializer consumes draws. Other unplanned reseeds still fail Verify.
 public static List<SeedBinding> RestoreEndCapSeeds(ParticleSystemSimulation root,List<SeedBinding> bindings) {
  var resetPaths=new HashSet<string>();
  var field=typeof(ParticleSystemSimulation).GetField("isEndCapChild",BindingFlags.Instance|BindingFlags.NonPublic)
   ??throw new Exception("Pinned endcap child contract changed");
  void Visit(ParticleSystemSimulation sim,string path,bool reset) {
   if(reset)resetPaths.Add(path);
   for(int index=0;index<sim.Children.Count;index++) {
    var child=sim.Children[index];
    if(field.GetValue(child) is not bool isEndCap)throw new Exception("Invalid pinned endcap flag");
    Visit(child,path+"/"+index,reset||isEndCap);
   }
  }
  Visit(root,"root",false);
  var restored=bindings.Where(binding=>resetPaths.Contains(binding.Path)).ToList();
  if(restored.Count!=resetPaths.Count)throw new Exception("Endcap seed provenance is incomplete");
  foreach(var binding in restored) {
   var counter=binding.Random.GetType().GetField("queryCount",BindingFlags.Instance|BindingFlags.NonPublic)
    ??throw new Exception("Pinned random counter contract changed");
   if(counter.GetValue(binding.Random) is not int draws||draws!=0)throw new Exception("Endcap random state was consumed before substitute seed restore");
   binding.SeedProperty.SetValue(binding.Random,binding.Value);binding.Verify();
  }
  return restored;
 }
 public static List<SeedBinding> Apply(ParticleSystemSimulation root,uint seed,string instance) {
  var result=new List<SeedBinding>();
  void Visit(ParticleSystemSimulation sim,string path) {
   var property=typeof(ParticleSystemState).GetProperty("Random",BindingFlags.Instance|BindingFlags.NonPublic)??throw new Exception("Pinned simulator Random contract changed");
   var random=property.GetValue(sim.RenderState)??throw new Exception("Missing simulator Random");
   var seedProperty=random.GetType().GetProperty("Seed")??throw new Exception("Missing seed property");
   var counter=random.GetType().GetField("queryCount",BindingFlags.Instance|BindingFlags.NonPublic)??throw new Exception("Missing random counter");
   if(seedProperty.PropertyType!=typeof(int)||counter.FieldType!=typeof(int)||counter.GetValue(random) is not int draws||draws!=0||seedProperty.GetSetMethod(true)==null)
    throw new Exception("Simulator random state is not pristine; cannot apply substitute seed");
   var key=seed.ToString(System.Globalization.CultureInfo.InvariantCulture)+"\n"+instance+"\n"+path+"\n"+sim.Name;
   var value=(int)(BinaryPrimitives.ReadUInt32LittleEndian(SHA256.HashData(Encoding.UTF8.GetBytes(key)))&0xFFF);
   seedProperty.SetValue(random,value);
   var binding=new SeedBinding{Path=path,Resource=sim.Name,Value=value,Random=random,SeedProperty=seedProperty};binding.Verify();result.Add(binding);
   for(int i=0;i<sim.Children.Count;i++)Visit(sim.Children[i],path+"/"+i);
  }
  Visit(root,"root");return result;
 }
}
class CaptureLog:ILogger {
 public List<string> Messages=new();public string Prefix="";
 public IDisposable? BeginScope<TState>(TState state) where TState:notnull=>null;
 public bool IsEnabled(LogLevel level)=>level>=LogLevel.Warning;
 public void Log<TState>(LogLevel level,EventId id,TState state,Exception? error,Func<TState,Exception?,string> format){if(IsEnabled(level))Messages.Add(Prefix+": "+format(state,error));}
}
