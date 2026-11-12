#if AGR_SIMULATION_SNAPSHOT
using System.Collections;
using System.Numerics;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Blocks;
using ValveResourceFormat.Particles;
using ValveResourceFormat.Particles.Utils;
using ValveResourceFormat.ResourceTypes;

static class SnapshotRuntimeOracle
{
    static readonly string Binding = new('a',64);
    static readonly Type Factory = typeof(ParticleSystemSimulation).Assembly.GetType("ValveResourceFormat.Particles.ParticleControllerFactory",true)!;
    static KVObject Def(string name) { var d=KVObject.Collection();d["_class"]=new(name);return d; }
    static object Create(string kind,string name,KVObject definition)
    {
        object?[] args=[name,definition,NullLogger.Instance,12,null];
        if(!(bool)Factory.GetMethod("TryCreate"+kind)!.Invoke(null,args)!)throw new Exception("Unregistered "+name);
        return args[^1]!;
    }
    static void Invoke(object op,string method,params object[] args) => op.GetType().GetMethod(method)!.Invoke(op,args);
    static Vector3 V(JsonElement a) {var v=a.EnumerateArray().Select(x=>x.GetSingle()).ToArray();return new(v[0],v[1],v[2]);}
    static SnapshotSkinningMatrix M(JsonElement a)=>new(a.EnumerateArray().Select(x=>x.GetSingle()).ToArray());
    static ParticleSnapshot Snapshot(Vector3[] points) => ParticleSnapshot.Create((uint)points.Length,
        new Dictionary<(string,string),IEnumerable> {
            [("position","float3")]=points.ToArray(),
            [("skinning","skinning")]=points.Select(_=>new ParticleSnapshot.SkinningData { JointNames=["crossbow","","",""],Weights=[1,0,0,0] }).ToArray()
        });
    static ParticleSystemSimulation Sim(Vector3[] points,int emitted,bool update=true,ParticleSystemState? parent=null,ParticleSnapshot? snapshot=null)
    {
        var init=Def("C_INIT_InitSkinnedPositionFromCPSnapshot");init["m_nSnapshotControlPointNumber"]=new(6);
        var emit=Def("C_OP_InstantaneousEmitter");emit["m_nParticlesToEmit"]=new(emitted);
        var d=Def("CParticleSystemDefinition");d["m_nBehaviorVersion"]=new(12);d["m_nSnapshotControlPoint"]=new(6);
        d["m_Emitters"]=KVObject.Array([emit]);d["m_Initializers"]=KVObject.Array([init]);
        d["m_Operators"]=KVObject.Array(update?[Def("C_OP_SnapshotRigidSkinToBones")]:[]);
        return new(ParticleSystem.Create(d),null!,NullLogger.Instance,snapshot??Snapshot(points),parent);
    }
    public static void Check(string input,string output)
    {
        using var doc=JsonDocument.Parse(File.ReadAllBytes(input));int initialized=0,updated=0,refused=0,lifeChecks=0;
        float maximum=0;
        void Compare(Vector3 a,Vector3 b,float tolerance=1e-4f)
        {
            var v=Vector3.Abs(a-b);var error=Math.Max(v.X,Math.Max(v.Y,v.Z));
            if(!float.IsFinite(error)||error>tolerance)throw new Exception($"Registered snapshot mismatch {a} vs {b}: {error}");
            maximum=Math.Max(maximum,error);
        }
        void Refuse(Action action)
        {
            try { action(); }
            catch(Exception error) when (error is ArgumentException or InvalidOperationException or NotSupportedException
                || error is TargetInvocationException { InnerException: ArgumentException or InvalidOperationException or NotSupportedException })
                { refused++;return; }
            throw new Exception("Unqualified snapshot runtime input accepted");
        }
        foreach(var row in doc.RootElement.GetProperty("initializers").EnumerateArray())
        {
            if(row.GetProperty("rigid").GetBoolean()||row.GetProperty("matrix").ValueKind==JsonValueKind.Null)continue;
            var matrix=M(row.GetProperty("matrix"));var sim=Sim([V(row.GetProperty("rest"))],1,update:false);
            sim.RenderState.SnapshotModelBoneProvider=(cp,name)=>cp==0&&name=="crossbow"?new(Binding,matrix):throw new Exception("Wrong model/bone");
            sim.Update(1e-6f,0);
            if(sim.Particles.Count!=1)throw new Exception("Snapshot initializer did not emit");
            var expected=row.GetProperty("expected");var p=sim.Particles.Current[0];
            Compare(p.Position,V(expected.GetProperty("position")));Compare(p.PositionPrevious,V(expected.GetProperty("previous")));
            Compare(sim.Particles.Initial[0].PositionPrevious,p.PositionPrevious);
            Compare(p.HitboxOffsetPosition,V(row.GetProperty("rest")));initialized++;
        }
        var op=Create("Operator","C_OP_SnapshotRigidSkinToBones",Def("C_OP_SnapshotRigidSkinToBones"));
        foreach(var row in doc.RootElement.GetProperty("updates").EnumerateArray())
        {
            if(row.GetProperty("matrix").ValueKind==JsonValueKind.Null)continue;
            var state=new ParticleSystemState();var matrix=M(row.GetProperty("matrix"));
            state.SnapshotModelBoneProvider=(cp,name)=>new(Binding,matrix);
            var particles=new ParticleCollection(new Particle(),1);particles.Add();
            particles.Current[0]=new Particle { Index=0,Position=V(row.GetProperty("before_position")),
                PositionPrevious=V(row.GetProperty("before_previous")),HitboxOffsetPosition=V(row.GetProperty("rest")),
                SnapshotSkinningInput=new(0,"crossbow",Binding) };
            particles.Initial[0].PositionPrevious=V(row.GetProperty("initial_previous"));
            Invoke(op,"Operate",particles,1/64f,state,1f);
            var expected=row.GetProperty("expected");Compare(particles.Current[0].Position,V(expected.GetProperty("position")));
            Compare(particles.Current[0].PositionPrevious,V(expected.GetProperty("previous")));updated++;
        }
        var identity=new SnapshotSkinningMatrix(new float[]{1,0,0,100,0,1,0,-20,0,0,1,50});
        var moved=new SnapshotSkinningMatrix(new float[]{0,-1,0,104,1,0,0,-21,0,0,1,53});
        var points=new[]{new Vector3(1,2,3),new Vector3(4,5,6),new Vector3(7,8,9)};
        var parent=new ParticleSystemState { SnapshotModelBoneProvider=(cp,name)=>new(Binding,identity) };
        var a=Sim(points,5,parent:parent);a.Update(1e-6f,0);
        var b=Sim(points,2,parent:parent);b.Update(1e-6f,0);
        for(int i=0;i<5;i++)
        {
            Compare(a.Particles.Current[i].HitboxOffsetPosition,points[i%3]);
            Compare(a.Particles.Initial[i].PositionPrevious,identity.TransformPoint(points[i%3]));lifeChecks++;
        }
        for(int i=0;i<2;i++){Compare(b.Particles.Current[i].HitboxOffsetPosition,points[i]);lifeChecks++;}
        parent.SnapshotModelBoneProvider=(cp,name)=>new(Binding,moved);a.Update(1/64f,0);
        for(int i=0;i<5;i++)
        {
            Compare(a.Particles.Current[i].Position,moved.TransformPoint(points[i%3]));
            Compare(a.Particles.Current[i].PositionPrevious,moved.TransformPoint(points[i%3]+a.Particles.Initial[i].PositionPrevious));lifeChecks++;
        }
        // Compaction must keep the immutable bone binding AND the corresponding
        // literal initial previous value beside each surviving particle.
        var oldId=a.Particles.Current[1].UniqueParticleId;var oldInitial=a.Particles.Initial[1].PositionPrevious;
        a.Particles.Current[0].MarkedAsKilled=true;a.Particles.PruneExpired();
        if(a.Particles.Current[0].UniqueParticleId!=oldId)throw new Exception("Compaction changed survivor identity");
        Compare(a.Particles.Initial[0].PositionPrevious,oldInitial);
        a.Update(1/64f,0);Compare(a.Particles.Current[0].PositionPrevious,moved.TransformPoint(points[1]+oldInitial));lifeChecks++;
        var slot=a.Particles.Add();
        if(slot<0||a.Particles.Initial[slot].SnapshotSkinningInput!=null)throw new Exception("Reused initial slot retained stale bone ownership");
        a.Particles.Current[slot]=a.Particles.Initial[slot];a.Particles.Current[slot].Index=slot;
        Refuse(()=>Invoke(op,"Operate",a.Particles,1/64f,a.RenderState,1f));
        a.Replay();a.Update(1e-6f,0);
        for(int i=0;i<5;i++){Compare(a.Particles.Current[i].HitboxOffsetPosition,points[i%3]);lifeChecks++;}
        parent.SnapshotModelBoneProvider=(cp,name)=>new(new string('b',64),moved);
        Refuse(()=>a.Update(1/64f,0));
        Refuse(()=>Sim(points,1).Update(1e-6f,0));
        Refuse(()=>Invoke(op,"Operate",b.Particles,1/64f,b.RenderState,.5f));
        var wrongCp=Def("C_OP_SnapshotRigidSkinToBones");wrongCp["m_nControlPointNumber"]=new(1);
        var other=Create("Operator","C_OP_SnapshotRigidSkinToBones",wrongCp);
        Refuse(()=>Invoke(other,"Operate",b.Particles,1/64f,b.RenderState,1f));
        foreach(var key in new[]{"m_bRandom","m_bRigid","m_bSetNormal","m_bIgnoreDt","m_bCopyColor","m_bCopyAlpha","m_bSetRadius"})
        {var d=Def("C_INIT_InitSkinnedPositionFromCPSnapshot");d[key]=new(true);Refuse(()=>Create("Initializer","C_INIT_InitSkinnedPositionFromCPSnapshot",d));}
        foreach(var key in new[]{"m_flBoneVelocity","m_flBoneVelocityMax","m_flMinNormalVelocity","m_flMaxNormalVelocity","m_flIncrement"})
        {var d=Def("C_INIT_InitSkinnedPositionFromCPSnapshot");d[key]=new(2f);Refuse(()=>Create("Initializer","C_INIT_InitSkinnedPositionFromCPSnapshot",d));}
        foreach(var key in new[]{"m_bTransformNormals","m_bTransformRadii"})
        {var d=Def("C_OP_SnapshotRigidSkinToBones");d[key]=new(true);Refuse(()=>Create("Operator","C_OP_SnapshotRigidSkinToBones",d));}
        foreach(var mode in new[]{"weights","names","columns","nonfinite"})
        {
            var snapshot=Snapshot(points);
            if(mode=="weights")((ParticleSnapshot.SkinningData[])snapshot.AttributeData[("skinning","skinning")])[0].Weights=[.5f,.5f,0,0];
            if(mode=="names")((ParticleSnapshot.SkinningData[])snapshot.AttributeData[("skinning","skinning")])[0].JointNames=["","","",""];
            if(mode=="columns")snapshot=ParticleSnapshot.Create(3,new Dictionary<(string,string),IEnumerable>{[("position","float3")]=points});
            if(mode=="nonfinite")((Vector3[])snapshot.AttributeData[("position","float3")])[0]=new(float.NaN,0,0);
            var sim=Sim(points,1,update:false,parent:parent,snapshot:snapshot);
            Refuse(()=>sim.Update(1e-6f,0));
        }
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",registered_native_initializer_rows=initialized,
            registered_native_update_rows=updated,maximum_position_error_source_units=maximum,lifecycle_checks=lifeChecks,
            invalid_input_refusals=refused,input_sha256=Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(input))),
            scope="Actual registered initializer/operator, native position-pair fixtures, full simulation spawn/initial copy/update/replay and collection compaction/reuse. Explicit synthetic model matrices; no normal-export acquisition or full scene proof."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Registered snapshot runtime PASS {initialized}native init/{updated}native update/{lifeChecks}lifecycle/{refused}refusals");
    }
}
#endif
