using System.Numerics;
using System.Reflection;
using System.Text.Json;
using ValveKeyValue;
using ValveResourceFormat.Particles;

static class TransformOracle
{
    public static void Check(string? output=null)
    {
        var assembly=typeof(ParticleSystemSimulation).Assembly;
        var parserType=assembly.GetType("ValveResourceFormat.Particles.ParticleDefinitionParser",true)!;
        var identityType=assembly.GetType("ValveResourceFormat.Particles.IdentityTransformProvider",true)!;
        var interfaceType=assembly.GetType("ValveResourceFormat.Particles.ITransformProvider",true)!;
        var identity=Activator.CreateInstance(identityType)!;
        int samples=0;
        foreach(var kind in new[]{"PT_TYPE_INVALID","PT_TYPE_NAMED_VALUE","PT_TYPE_CONTROL_POINT_RANGE","AGR_UNKNOWN_TRANSFORM"})
        {
            var transform=KVObject.Collection();transform["m_nType"]=new KVObject(kind);
            var kv=KVObject.Collection();kv["m_TransformInput"]=transform;
            var log=new CaptureLog();var parser=Activator.CreateInstance(parserType,kv,log,0)!;
            var method=parserType.GetMethods().Single(m=>m.Name=="TransformInput"&&m.GetParameters().Length==2);
            var provider=method.Invoke(parser,["m_TransformInput",identity])!;
            if(kind!="PT_TYPE_INVALID")
            {
                if(log.Messages.Count==0)throw new Exception("Unsupported transform diagnostic suppressed: "+kind);
                continue;
            }
            if(log.Messages.Count!=0)throw new Exception("Apply the PT_TYPE_INVALID identity patch before building ParticleProbe");
            foreach(float time in new[]{-1f,0f,.25f,10f})
            {
                object[] args=[new Particle(),new ParticleSystemState(),time];
                var result=(Matrix4x4)interfaceType.GetMethod("NextTransformAtTime")!.Invoke(provider,args)!;
                if(result!=Matrix4x4.Identity)throw new Exception("Invalid transform is not identity");
                samples++;
            }
        }
        if(output!=null)File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",identity_samples=samples,
            unknown_diagnostics_preserved=true,scope="Actual parser/provider contract; engine evidence in invalid-transform-oracle.json"},new JsonSerializerOptions{WriteIndented=true}));
    }
}
