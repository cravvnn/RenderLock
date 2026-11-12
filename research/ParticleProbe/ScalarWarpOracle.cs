#if AGR_SIMULATION_HITBOX
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;

/// <summary>Real registered scalar-warp initializer against prepared native rows.</summary>
static class ScalarWarpOracle
{
    static Vector3 V(JsonElement row)=>new(row[0].GetSingle(),row[1].GetSingle(),row[2].GetSingle());
    static KVObject Vec(Vector3 v)=>KVObject.Array([new(v.X),new(v.Y),new(v.Z)]);
    static KVObject Def()=>KVObject.Collection();
    static object Create(KVObject definition)
    {
        var factory=typeof(ParticleSystemSimulation).Assembly.GetType("ValveResourceFormat.Particles.ParticleControllerFactory",true)!;
        object?[] args=["C_INIT_PositionWarpScalar",definition,NullLogger.Instance,12,null];
        if(!(bool)factory.GetMethod("TryCreateInitializer")!.Invoke(null,args)!)throw new Exception("Scalar warp was not registered");
        return args[^1]!;
    }
    static Particle Apply(object op,Particle particle,ParticleSystemState state)
    {
        var collection=new ParticleCollection(new Particle(),1);collection.Add();
        object[] args=[particle,collection,state];
        var value=(Particle)op.GetType().GetMethod("Initialize")!.Invoke(op,args)!;
        var written=(Particle)args[0];
        if(value.Position!=written.Position||value.PositionPrevious!=written.PositionPrevious)throw new Exception("Scalar warp returned another particle state");
        return value;
    }
    public static void Check(string input,string output)
    {
        using var doc=JsonDocument.Parse(File.ReadAllBytes(input));int checkedRows=0,refusals=0,numberRows=0;
        float maximum=0;
        void Compare(Vector3 value,Vector3 expected)
        {
            var error=Vector3.Abs(value-expected);var delta=MathF.Max(error.X,MathF.Max(error.Y,error.Z));
            if(!float.IsFinite(delta)||delta>.04f)throw new Exception($"Scalar warp native mismatch {value} versus {expected}: {delta}");
            maximum=MathF.Max(maximum,delta);
        }
        foreach(var row in doc.RootElement.GetProperty("rows").EnumerateArray())
        {
            var definition=Def();definition["_class"]=new("C_INIT_PositionWarpScalar");
            definition["m_vecWarpMin"]=Vec(V(row.GetProperty("minimum")));definition["m_vecWarpMax"]=Vec(V(row.GetProperty("maximum")));
            definition["m_flPrevPosScale"]=new(row.GetProperty("prevscale").GetSingle());
            definition["m_nScaleControlPointNumber"]=new(row.GetProperty("scale_cp").GetBoolean()?5:-1);
            var scalar=Def();scalar["m_nType"]=new("PF_TYPE_LITERAL");scalar["m_flLiteralValue"]=new(row.GetProperty("input").GetSingle());definition["m_InputValue"]=scalar;
            var op=Create(definition);var state=new ParticleSystemState();var frame=row.GetProperty("frame");
            var rotation=new Matrix4x4(frame[0][0].GetSingle(),frame[1][0].GetSingle(),frame[2][0].GetSingle(),0,
                frame[0][1].GetSingle(),frame[1][1].GetSingle(),frame[2][1].GetSingle(),0,
                frame[0][2].GetSingle(),frame[1][2].GetSingle(),frame[2][2].GetSingle(),0,0,0,0,1);
            state.GetControlPoint(0).Position=new(frame[0][3].GetSingle(),frame[1][3].GetSingle(),frame[2][3].GetSingle());
            state.GetControlPoint(0).Rotation=Quaternion.CreateFromRotationMatrix(rotation);
            state.GetControlPoint(5).Position=V(row.GetProperty("scale"));
            var particle=new Particle{Index=row.GetProperty("index").GetInt32(),Position=V(row.GetProperty("position")),PositionPrevious=V(row.GetProperty("previous")),Radius=123,Velocity=new(1,2,3)};
            var result=Apply(op,particle,state);
            Compare(result.Position,V(row.GetProperty("actual")[0]));Compare(result.PositionPrevious,V(row.GetProperty("actual")[1]));
            if(result.Radius!=123||result.Velocity!=particle.Velocity||result.Index!=particle.Index)throw new Exception("Scalar warp changed an unrelated field");
            checkedRows++;
        }
        // Existing provider framework + real initializer: this checks the exact
        // authored first-emission number remap, separately from native kernels.
        foreach(var number in new[]{0,1,50,99,100,101,200})
        {
            var d=Def();d["_class"]=new("C_INIT_PositionWarpScalar");d["m_vecWarpMax"]=Vec(new(1,1,15));
            var scalar=Def();scalar["m_nType"]=new("PF_TYPE_PARTICLE_NUMBER");scalar["m_nMapType"]=new("PF_MAP_TYPE_REMAP");
            scalar["m_flInput0"]=new(0f);scalar["m_flInput1"]=new(100f);scalar["m_flOutput0"]=new(0f);scalar["m_flOutput1"]=new(1f);
            scalar["m_nInputMode"]=new("PF_INPUT_MODE_CLAMPED");d["m_InputValue"]=scalar;
            var state=new ParticleSystemState();var result=Apply(Create(d),new Particle{UniqueParticleId=number,Position=new(2,3,4),PositionPrevious=new(5,6,7)},state);
            var scale=1+14*Math.Clamp(number/100f,0,1);Compare(result.Position,new(2,3,4*scale));Compare(result.PositionPrevious,new(5,6,7*scale));numberRows++;
        }
        void Refuse(Action action)
        {
            try{action();}
            catch(Exception e)when(e is NotSupportedException||e is TargetInvocationException{InnerException:NotSupportedException}){refusals++;return;}
            throw new Exception("Invalid scalar warp accepted");
        }
        foreach(var cp in new[]{-2,64})
        {var d=Def();d["_class"]=new("C_INIT_PositionWarpScalar");d["m_nControlPointNumber"]=new(cp);Refuse(()=>Create(d));}
        foreach(var cp in new[]{-2,64})
        {var d=Def();d["_class"]=new("C_INIT_PositionWarpScalar");d["m_nScaleControlPointNumber"]=new(cp);Refuse(()=>Create(d));}
        var badState=new ParticleSystemState();badState.GetControlPoint(0).Rotation=new(0,0,0,2);
        Refuse(()=>Apply(Create(Def()),new Particle(),badState));
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",native_rows=checkedRows,maximum_error_source_units=maximum,number_provider_rows=numberRows,invalid_refusals=refusals,
            scope="Actual registered initializer with native prepared interpolation/scale/current/previous cases and existing particle-number remap. Native acquisition/provider evaluator and full scene are separate."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Scalar warp registered PASS {checkedRows}native/{numberRows}provider/{refusals}refusals max {maximum}");
    }
}

#endif
