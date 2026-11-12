#if AGR_SIMULATION_HITBOX
using System.Collections.Immutable;
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;

static class HitboxRuntimeOracle
{
    static Vector3 V(JsonElement row) { var v=row.EnumerateArray().Select(x=>x.GetSingle()).ToArray();return new(v[0],v[1],v[2]); }
    static ImmutableArray<AuthoredHitboxShape> Shapes(JsonElement data)
    {
        var low=data.GetProperty("mins").EnumerateArray().ToArray();var high=data.GetProperty("maxs").EnumerateArray().ToArray();
        return data.GetProperty("transforms").EnumerateArray().Select((row,i)=>{
            var p=row.EnumerateArray().Select(x=>x.GetSingle()).ToArray();
            return new AuthoredHitboxShape(V(low[i]),V(high[i]),new(p[0],p[1],p[2]),new(p[4],p[5],p[6],p[7]),p[3],1,-1);
        }).ToImmutableArray();
    }
    static KVObject KV(JsonElement node)
    {
        switch(node.ValueKind)
        {
            case JsonValueKind.Object:var result=KVObject.Collection();foreach(var item in node.EnumerateObject())result[item.Name]=KV(item.Value);return result;
            case JsonValueKind.Array:return KVObject.Array(node.EnumerateArray().Select(KV).ToArray());
            case JsonValueKind.String:return new KVObject(node.GetString()!);
            case JsonValueKind.True:return new KVObject(true);
            case JsonValueKind.False:return new KVObject(false);
            case JsonValueKind.Number:return node.TryGetInt64(out var value)?new KVObject(value):new KVObject(node.GetSingle());
            default:throw new Exception("Unsupported fixture value");
        }
    }
    public static void Check(string folder,string output)
    {
        var assembly=typeof(ParticleSystemSimulation).Assembly;
        var parser=assembly.GetType("ValveResourceFormat.Particles.ParticleDefinitionParser",true)!;
        var opType=assembly.GetType("ValveResourceFormat.Particles.Operators.LockToBone",true)!;
        var initType=assembly.GetType("ValveResourceFormat.Particles.Initializers.CreateOnModel",true)!;
        float maximum=0;int locked=0,sampled=0,operators=0,constructors=0,rejected=0,explicitTransforms=0;
        void Compare(Vector3 actual,Vector3 expected,float tolerance)
        {
            var d=Vector3.Abs(actual-expected);var error=Math.Max(d.X,Math.Max(d.Y,d.Z));
            if(!float.IsFinite(error)||error>tolerance)throw new Exception($"Native hitbox mismatch: {actual} vs {expected}, error{error}");
            maximum=Math.Max(maximum,error);
        }
        using var locks=JsonDocument.Parse(File.ReadAllBytes(Path.Combine(folder,"lock-fixtures.json")));
        foreach(var row in locks.RootElement.GetProperty("cases").EnumerateArray())
        {
            var frame=new HitboxFrameHistory.Frame(1,"fixture",Shapes(row.GetProperty("current")),Shapes(row.GetProperty("previous")));
            var pos=row.GetProperty("positions").EnumerateArray().ToArray();var previous=row.GetProperty("previous_positions").EnumerateArray().ToArray();
            var uv=row.GetProperty("uv").EnumerateArray().ToArray();var ids=row.GetProperty("indices").EnumerateArray().Select(x=>x.GetInt32()).ToArray();
            var ages=row.GetProperty("ages").EnumerateArray().Select(x=>x.GetSingle()).ToArray();var native=row.GetProperty("native").EnumerateArray().ToArray();
            var expected=native[0].EnumerateArray().ToArray();var expectedPrevious=native[1].EnumerateArray().ToArray();
            float F(string key)=>row.GetProperty(key).GetSingle();bool jumped=row.GetProperty("jumped").GetBoolean();
            var kv=KVObject.Collection();kv["m_flLifeTimeFadeStart"]=new(F("fade_start"));kv["m_flLifeTimeFadeEnd"]=new(F("fade_end"));kv["m_flPrevPosScale"]=new(F("previous_scale"));
            var op=Activator.CreateInstance(opType,Activator.CreateInstance(parser,kv,NullLogger.Instance,0)!)!;
            var state=new ParticleSystemState();int calls=0;
            state.ModelHitboxProvider=(consumer,cp,set,weighted)=>{if(consumer!=state||cp!=0||set!=""||weighted)throw new Exception("Wrong CP history request");calls++;return frame;};
            state.GetControlPoint(0).Position=jumped?new Vector3(11,0,0):Vector3.Zero;
            var particles=new ParticleCollection(new Particle(),pos.Length);
            for(int i=0;i<pos.Length;i++)
            {
                var actual=ModelHitboxSampling.Lock(frame,V(pos[i]),V(previous[i]),ids[i],V(uv[i]),ages[i],F("dt"),F("fade_start"),F("fade_end"),F("strength"),F("previous_scale"),jumped);
                Compare(actual.Position,V(expected[i]),1e-4f);Compare(actual.Previous,V(expectedPrevious[i]),1e-4f);locked++;
                particles.Add();ref var p=ref particles.Current[i];p.Position=V(pos[i]);p.PositionPrevious=V(previous[i]);p.Age=ages[i];p.HitboxIndex=ids[i];p.HitboxOffsetPosition=V(uv[i]);
            }
            opType.GetMethod("Operate")!.Invoke(op,[particles,F("dt"),state,F("strength")]);
            if(calls!=1)throw new Exception("Registered operator did not consume explicit provider");
            for(int i=0;i<pos.Length;i++){Compare(particles.Current[i].Position,V(expected[i]),1e-4f);Compare(particles.Current[i].PositionPrevious,V(expectedPrevious[i]),1e-4f);operators++;}
        }
        using var samples=JsonDocument.Parse(File.ReadAllBytes(Path.Combine(folder,"sample-fixtures.json")));
        foreach(var row in samples.RootElement.GetProperty("cases").EnumerateArray())
        {
            var shapes=row.GetProperty("shapes").EnumerateArray().Select(s=>{
                var q=s.GetProperty("rotation").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
                return new AuthoredHitboxShape(V(s.GetProperty("low")),V(s.GetProperty("high")),V(s.GetProperty("translation")),new(q[0],q[1],q[2],q[3]),s.GetProperty("scale").GetSingle(),s.GetProperty("weight").GetSingle(),-1);
            }).ToImmutableArray();
            var draws=row.GetProperty("draws").EnumerateArray().ToArray();int cursor=0;
            float Draw(bool extra){var d=draws[cursor++];if(d[0].GetBoolean()!=extra)throw new Exception("Hitbox RNG stream mismatch");return d[1].GetSingle();}
            var native=row.GetProperty("native").EnumerateArray().ToArray();var pos=native[0].EnumerateArray().ToArray();var uvs=native[1].EnumerateArray().ToArray();var ids=native[2].EnumerateArray().ToArray();
            for(int i=0;i<pos.Length;i++)
            {
                var s=ModelHitboxSampling.Sample(shapes,V(row.GetProperty("origin")),V(row.GetProperty("scale")),V(row.GetProperty("bias")),row.GetProperty("weighted").GetBoolean(),Draw);
                Compare(s.Position,V(pos[i]),.001f);Compare(s.Coordinates,V(uvs[i]),1e-6f);if(s.Index!=ids[i].GetInt32())throw new Exception("Hitbox index mismatch");sampled++;
            }
            if(cursor!=row.GetProperty("counter").GetInt32())throw new Exception("Hitbox RNG count mismatch");
        }
        using var definitions=JsonDocument.Parse(File.ReadAllBytes(Path.Combine(folder,"definitions.json")));
        foreach(var definition in definitions.RootElement.GetProperty("definitions").EnumerateObject())
        {
            if(!definition.Value.TryGetProperty("m_Initializers",out var inits))continue;
            foreach(var init in inits.EnumerateArray())
            {
                if(init.GetProperty("_class").GetString()!="C_INIT_CreateOnModel")continue;
                var instance=Activator.CreateInstance(initType,Activator.CreateInstance(parser,KV(init),NullLogger.Instance,0)!)!;
                var modelCp=init.TryGetProperty("m_modelInput",out var model)?model.GetProperty("m_nControlPoint").GetInt32():0;
                var transformCp=init.TryGetProperty("m_transformInput",out var transform)?transform.GetProperty("m_nControlPoint").GetInt32():0;
                if((int)initType.GetField("transformControlPoint",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(instance)! !=transformCp)throw new Exception("Wrong transform CP");
                if(init.TryGetProperty("m_transformInput",out _))explicitTransforms++;
                constructors++;
                // Actual initializer must reject absent hitboxes, then consume
                // a deliberately supplied CP0/core fixture on the positive path.
                var method=initType.GetMethod("Initialize")!;var state=new ParticleSystemState();var particles=new ParticleCollection(new Particle(),1);
                try {method.Invoke(instance,[new Particle(),particles,state]);throw new Exception("Missing hitbox provider accepted");}
                catch(TargetInvocationException e) when(e.InnerException is InvalidOperationException){}
                state.ModelHitboxProvider=(consumer,cp,set,weighted)=>{
                    if(consumer!=state||cp!=modelCp)throw new Exception("Model CP was aliased to emission CP");
                    return new(0,"fixture",ImmutableArray.Create(new AuthoredHitboxShape(new(-1),new(1),new(5,6,7),Quaternion.Identity,1,1,-1)),[]);
                };
                state.GetControlPoint(0).Position=new(1000,2000,3000);
                state.GetControlPoint(1).Position=new(-100,-200,-300);
                state.SetControlPointRotation(1,Quaternion.CreateFromAxisAngle(Vector3.UnitY,1.1f));
                var actual=(Particle)method.Invoke(instance,[new Particle(),particles,state])!;
                if(actual.HitboxIndex!=0||!float.IsFinite(actual.Position.X)||actual.Position!=actual.PositionPrevious)throw new Exception("Authored initializer failed");
            }
        }
        foreach(var bad in new[]{"{\"m_nType\":\"PT_TYPE_INVALID\"}","{\"m_nType\":\"PT_TYPE_NAMED_VALUE\"}","{\"m_nType\":\"PT_TYPE_CONTROL_POINT_RANGE\"}","{\"m_nControlPoint\":-1}","{\"m_nControlPoint\":64}","{\"m_bUseOrientation\":false}","{\"m_bFollowNamedValue\":true}","{\"m_NamedValue\":\"other\"}"})
        {
            using var input=JsonDocument.Parse(bad);var kv=KVObject.Collection();kv["m_transformInput"]=KV(input.RootElement);
            try{Activator.CreateInstance(initType,Activator.CreateInstance(parser,kv,NullLogger.Instance,0)!);throw new Exception("Unsupported transform accepted");}
            catch(TargetInvocationException e)when(e.InnerException is NotSupportedException){rejected++;}
        }
        var system=KVObject.Collection();system["_class"]=new("CParticleSystemDefinition");system["m_nBehaviorVersion"]=new(12);
        var emitter=KVObject.Collection();emitter["_class"]=new("C_OP_InstantaneousEmitter");emitter["m_nParticlesToEmit"]=new(1);
        system["m_Emitters"]=KVObject.Array([emitter]);
        var body=definitions.RootElement.GetProperty("definitions").GetProperty("particles/abilities/vampirebat/vampirebat_blink_end_flash_body.vpcf");
        var bodyInit=body.GetProperty("m_Initializers").EnumerateArray().Single(x=>x.GetProperty("_class").GetString()=="C_INIT_CreateOnModel");
        var bodyLock=body.GetProperty("m_Operators").EnumerateArray().Single(x=>x.GetProperty("_class").GetString()=="C_OP_LockToBone");
        system["m_Initializers"]=KVObject.Array([KV(bodyInit)]);system["m_Operators"]=KVObject.Array([KV(bodyLock)]);
        var log=new CaptureLog();var sim=new ParticleSystemSimulation(ParticleSystem.Create(system),null!,log);
        var history=new HitboxFrameHistory();float shift=0;int frameQueries=0;
        sim.RenderState.ModelHitboxProvider=(consumer,cp,set,weighted)=>{
            if(consumer!=sim.RenderState||cp!=0||set!="core"||weighted)throw new Exception("Wrong simulation hitbox binding");
            frameQueries++;
            return history.Query(cp,consumer.Age,"prepared-Mina",ImmutableArray.Create(new AuthoredHitboxShape(new(-1),new(1),new(shift,0,0),Quaternion.Identity,1,1,-1)));
        };
        ControlPointAudit.BeginInputs(sim,[0]);sim.Start();sim.Update(1f/64,0);
        if(sim.Particles.Current.Length!=1||log.Messages.Count!=0)throw new Exception("Actual simulation did not emit one supported body particle");
        var initial=sim.Particles.Current[0].Position;
        foreach(var movement in new[]{5f,10f}){shift=movement;sim.Update(1f/64,movement);Compare(sim.Particles.Current[0].Position,initial+new Vector3(movement,0,0),1e-5f);}
        var legacy=KVObject.Collection();legacy["m_bUseBones"]=new(true);
        var legacyInit=Activator.CreateInstance(initType,Activator.CreateInstance(parser,legacy,NullLogger.Instance,0)!)!;
        var legacyState=new ParticleSystemState();legacyState.ModelBoneProvider=cp=>cp==0?[new ModelBonePose(Vector3.Zero,Quaternion.Identity,1)]:throw new Exception("Legacy CP alias");
        var legacyParticle=(Particle)initType.GetMethod("Initialize")!.Invoke(legacyInit,[new Particle(),new ParticleCollection(new Particle(),1),legacyState])!;
        if(legacyParticle.HitboxIndex!=0||legacyParticle.Position!=legacyParticle.PositionPrevious||!float.IsFinite(legacyParticle.Position.X))throw new Exception("Legacy bone-only initializer regressed");
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",locked,sampled,operator_particles=operators,authored_initializers=constructors,explicit_transforms=explicitTransforms,unsupported_transform_rejections=rejected,max_error=maximum,actual_simulation_frames=3,simulation_provider_queries=frameQueries,legacy_bone_initializer_pass=true,scope="Prepared native sampler/kernel comparisons, actual LockToBone and initializer calls plus actual miniature simulation. No game acquisition or full scene simulation qualification."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"C# hitbox oracle PASS: {locked} locks, {sampled} samples, {operators} operator particles");
    }
}

#endif
