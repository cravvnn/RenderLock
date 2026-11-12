#if AGR_SIMULATION_BATCH
using System.Collections;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;

static class SpawnBatchOracle
{
    static readonly BindingFlags Hidden=BindingFlags.NonPublic|BindingFlags.Instance;
    static KVObject D(string name){var d=KVObject.Collection();d["_class"]=new(name);return d;}
    static KVObject Literal(float value){var v=KVObject.Collection();v["m_nType"]=new("PF_TYPE_LITERAL");v["m_flLiteralValue"]=new(value);return v;}
    static KVObject Burst(int count,float time)
    {var d=D("C_OP_InstantaneousEmitter");d["m_nParticlesToEmit"]=new(count);d["m_flStartTime"]=Literal(time);return d;}
    static ParticleSystemSimulation Sim(int count,int version=12,int capacity=0,int initial=0,int later=0,bool badInitializer=false)
    {
        var input=KVObject.Collection();input["m_nType"]=new("PF_TYPE_PARTICLE_NUMBER_NORMALIZED");
        var init=D("C_INIT_InitFloat");init["m_nOutputField"]=new(18);init["m_InputValue"]=input;
        var definition=D("CParticleSystemDefinition");definition["m_nBehaviorVersion"]=new(version);
        definition["m_nMaxParticles"]=new(capacity);definition["m_nInitialParticles"]=new(initial);
        definition["m_Initializers"]=KVObject.Array([init]);
        if(badInitializer)
        {
            var snapshot=D("C_INIT_InitSkinnedPositionFromCPSnapshot");snapshot["m_nSnapshotControlPointNumber"]=new(6);
            definition["m_Initializers"]=KVObject.Array([init,snapshot]);
        }
        definition["m_Emitters"]=KVObject.Array(later>0?[Burst(count,0),Burst(later,1)]:count>0?[Burst(count,0)]:[]);
        return new(ParticleSystem.Create(definition),null!,NullLogger.Instance);
    }
    static object[] Functions(ParticleSystemSimulation sim,string name)=>(typeof(ParticleSystemSimulation).GetField(name,Hidden)!.GetValue(sim)as IEnumerable)!.Cast<object>().ToArray();
    static FieldInfo Pending=>typeof(ParticleSystemSimulation).GetField("emissionBatch",Hidden)??throw new Exception("Batch ownership field missing");
    static void Detached(ParticleSystemSimulation sim)
    {if(Pending.GetValue(sim)is not null)throw new Exception("Emitter callback leaked outside batch scope");}
    static void Near(float a,float b)
    {if(!float.IsFinite(a)||MathF.Abs(a-b)>2e-7f)throw new Exception($"Batch/native value mismatch: actual{a}, expected{b}");}
    static void Values(ParticleSystemSimulation sim,params float[] expected)
    {
        if(sim.Particles.Count!=expected.Length||sim.RenderState.ParticleCount!=expected.Length)throw new Exception("Batch count differs");
        for(int i=0;i<expected.Length;i++)
        {Near(sim.Particles.Current[i].ScratchFloat0,expected[i]);Near(sim.Particles.Initial[i].ScratchFloat0,expected[i]);if(sim.Particles.Current[i].Index!=i)throw new Exception("Invalid batch slot");}
        Detached(sim);
    }
    static void Refuse(Action action)
    {
        try{action();}
        catch(Exception e)when(e is InvalidOperationException or ArgumentException or NotSupportedException
            ||e is TargetInvocationException{InnerException:InvalidOperationException or ArgumentException or NotSupportedException}){return;}
        throw new Exception("Invalid batch operation accepted");
    }
    public static void Check(string input,string output)
    {
        using var native=JsonDocument.Parse(File.ReadAllBytes(input));int rows=0,values=0,lifecycle=0;
        ParticleSystemSimulation? lastNative=null;
        foreach(var row in native.RootElement.GetProperty("rows").EnumerateArray())
        {
            int count=row.GetProperty("count").GetInt32(),version=row.GetProperty("version").GetInt32();
            var sim=Sim(count,version,Math.Max(count,1));sim.Start();sim.Update(1e-6f,0);
            if(sim.BehaviorVersion!=version||sim.Particles.Count!=count)throw new Exception("Prepared batch/version mismatch");
            int index=row.GetProperty("first_block").GetInt32()*4;
            foreach(var v in row.GetProperty("values").EnumerateArray())
            {
                if(index<count){Near(sim.Particles.Current[index].ScratchFloat0,v.GetSingle());Near(sim.Particles.Initial[index].ScratchFloat0,v.GetSingle());values++;}
                index++;
            }
            lastNative=sim;rows++;
        }
        Detached(lastNative??throw new Exception("Native batch fixture is empty"));
        var burst=Sim(3,capacity:5,later:2);burst.Start();burst.Update(1e-6f,0);Values(burst,0,.5f,1);lifecycle++;
        burst.Update(1.1f,1.1f);Values(burst,0,.5f,1,.75f,1);lifecycle++;
        for(int i=0;i<5;i++)if(burst.Particles.Current[i].UniqueParticleId!=i)throw new Exception("Spawn identities changed");
        lifecycle++;
        var capped=Sim(5,capacity:3);capped.Start();capped.Update(1e-6f,0);Values(capped,0,.5f,1);lifecycle++;
        capped.Replay();capped.Update(1e-6f,0);Values(capped,0,.5f,1);lifecycle++;
        for(int i=0;i<3;i++)if(capped.Particles.Current[i].UniqueParticleId!=i)throw new Exception("Replay identity did not reset");
        lifecycle++;
        var seeded=Sim(0,capacity:3,initial:5);seeded.Start();Values(seeded,0,.5f,1);lifecycle++;
        var other=Sim(2);other.Start();other.Update(1e-6f,0);Values(other,0,1);Values(capped,0,.5f,1);lifecycle++;
        var prune=Sim(3,capacity:5,later:2);prune.Start();prune.Update(1e-6f,0);prune.Particles.Current[0].MarkedAsKilled=true;
        prune.Update(.01f,.01f);Values(prune,.5f,1);lifecycle++;
        prune.Update(1.1f,1.11f);Values(prune,.5f,1,2/3f,1);lifecycle++;
        if(!prune.Particles.Current.ToArray().Select(p=>p.UniqueParticleId).SequenceEqual(new[]{1,2,3,4}))throw new Exception("Prune/reuse identities changed");
        lifecycle++;
        var signed=Sim(0,capacity:3);typeof(ParticleSystemSimulation).GetMethod("EmitBatch",Hidden)!.Invoke(signed,[new float[]{-.25f,.5f}]);
        Near(signed.Particles.Current[0].Age,-.25f);Near(signed.Particles.Current[0].CreationTime,.25f);
        Near(signed.Particles.Current[1].Age,.5f);Near(signed.Particles.Current[1].CreationTime,-.5f);lifecycle++;
        var collect=typeof(ParticleSystemSimulation).GetMethod("EmitParticle",Hidden)!;
        Refuse(()=>collect.Invoke(other,[.2f]));lifecycle++;
        var owned=new List<float>();Pending.SetValue(other,owned);
        Refuse(()=>collect.Invoke(other,[float.NaN]));if(owned.Count!=0)throw new Exception("Invalid age queued");
        Pending.SetValue(other,null);Detached(other);lifecycle++;
        Refuse(()=>typeof(ParticleSystemSimulation).GetMethod("EmitBatch",Hidden)!.Invoke(other,[new float[5000]]));lifecycle++;
        var failed=Sim(3);failed.Start();var emitter=Functions(failed,"emitters")[0];var callback=emitter.GetType().GetField("particleEmitCallback",Hidden)!;
        var original=(Action<float>)callback.GetValue(emitter)!;
        callback.SetValue(emitter,new Action<float>(age=>{original(age);throw new InvalidOperationException("Fixture emission failure");}));
        Refuse(()=>failed.Update(1e-6f,0));Detached(failed);if(failed.Particles.Count!=0)throw new Exception("Failed emitter published pending slots");lifecycle++;
        var badInit=Sim(3,badInitializer:true);badInit.Start();Refuse(()=>badInit.Update(1e-6f,0));Detached(badInit);lifecycle++;
        Values(other,0,1);lifecycle++;
        var warm=Sim(0,capacity:3);warm.RenderState.ParticleCount=17;
        var scope=warm.BeginPrewarm(5)??throw new Exception("Empty prewarm returned no scope");
        Values(warm,0,.5f,1);warm.EndPrewarm(scope);
        if(warm.Particles.Count!=0||warm.RenderState.ParticleCount!=17)throw new Exception("Prewarm counters not restored");
        var again=warm.BeginPrewarm(2)??throw new Exception("Repeated prewarm returned no scope");
        if(warm.Particles.Current[0].UniqueParticleId!=0||warm.Particles.Current[1].UniqueParticleId!=1)throw new Exception("Prewarm serial changed");
        warm.EndPrewarm(again);
        if(warm.RenderState.ParticleCount!=17||warm.Particles.Count!=0)throw new Exception("Repeated prewarm changed counters");
        var nonempty=Sim(2);nonempty.Start();nonempty.Update(1e-6f,0);
        if(nonempty.BeginPrewarm(2)is not null)throw new Exception("Nonempty collection prewarmed");
        Values(nonempty,0,1);
        var nan=Sim(0,capacity:3);
        Refuse(()=>typeof(ParticleSystemSimulation).GetMethod("EmitBatch",Hidden)!.Invoke(nan,[new float[]{float.NaN}]));
        if(nan.Particles.Count!=0)throw new Exception("Invalid direct batch published slots");
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",prewarm_checks=3,direct_invalid_age_guard=true,native_rows=rows,native_values=values,lifecycle_checks=lifecycle,
            scope="Actual registered normalized initializer matches prepared native count/version rows; existing+new batches, capacity, initial burst, replay, prune/reuse, signed ages, independent contexts and fatal exception callback cleanup. No native whole emitter scheduling/random-order or game scene parity."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Spawn batch PASS {rows}native rows/{values}values/{lifecycle}lifecycle checks");
    }
}

#endif
