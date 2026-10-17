using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;

static class RampSplineOracle
{
    public static void Check(string input, string output)
    {
        using var document=JsonDocument.Parse(File.ReadAllText(input));
        var assembly=typeof(ParticleSystemSimulation).Assembly;
        var parserType=assembly.GetType("ValveResourceFormat.Particles.ParticleDefinitionParser",true)!;
        var operatorType=assembly.GetType("ValveResourceFormat.Particles.Operators.RampScalarSpline",true)!;
        var results=new List<object>();float maximum=0;int lanes=0;
        foreach(var row in document.RootElement.GetProperty("cases").EnumerateArray())
        {
            var kv=KVObject.Collection();
            bool defaults=row.TryGetProperty("use_authored_defaults",out var flag)&&flag.GetBoolean();
            foreach(var (name,key) in new[]{("rate","m_Rate"),("start","m_flStartTime_"),("end","m_flEndTime_")})
            {
                if(defaults&&name!="rate")continue;
                var values=row.GetProperty(name).EnumerateArray().Select(x=>x.GetSingle()).ToArray();
                kv[key+(name=="rate"?"Min":"min")]=new KVObject(values[0]);
                kv[key+(name=="rate"?"Max":"max")]=new KVObject(values[1]);
            }
            if(!defaults){
                kv["m_flBias"]=new KVObject(row.GetProperty("bias").GetSingle());
                kv["m_bProportionalOp"]=new KVObject(row.GetProperty("proportional").GetBoolean());
            }
            kv["m_bEaseOut"]=new KVObject(row.GetProperty("ease_out").GetBoolean());
            int field=row.GetProperty("field").GetInt32();kv["m_nField"]=new KVObject(field);
            var parser=Activator.CreateInstance(parserType,kv,NullLogger.Instance,0)!;
            var op=Activator.CreateInstance(operatorType,parser)!;
            var state=new ParticleSystemState();
            var random=typeof(ParticleSystemState).GetProperty("Random",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(state)!;
            random.GetType().GetProperty("Seed")!.SetValue(random,23);
            random.GetType().GetProperty("OperatorOffset")!.SetValue(random,17);
            var particles=new ParticleCollection(new Particle(),4);
            var ages=row.GetProperty("ages").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
            var lives=row.GetProperty("lives").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
            var initial=row.GetProperty("input").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
            var expected=row.GetProperty("actual").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
            int[] ids=[0,1,23,1023];
            for(int i=0;i<4;i++)
            {
                particles.Add();ref var p=ref particles.Current[i];
                p.ParticleId=ids[i];p.Age=ages[i];p.Lifetime=lives[i];
                p.Radius=initial[i];p.Alpha=initial[i];p.Rotation=new Vector3(initial[i],0,initial[i]);
            }
            operatorType.GetMethod("Operate")!.Invoke(op,[particles,1f/64,state,.75f]);
            float[] actual=new float[4];
            for(int i=0;i<4;i++)
            {
                ref var p=ref particles.Current[i];
                actual[i]=field switch {3=>p.Radius,7=>p.Alpha,4=>p.Rotation.Z,12=>p.Rotation.X,_=>throw new Exception("Unknown oracle field")};
                float error=MathF.Abs(actual[i]-expected[i]);
                if(!float.IsFinite(error)||error>5e-5f)throw new Exception($"Spline oracle mismatch lane{i}: {actual[i]} != {expected[i]}");
                maximum=Math.Max(maximum,error);lanes++;
            }
            results.Add(new{actual,expected});
        }
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",lanes,max_error=maximum,
            scope="Actual C# operator vs independently emulated installed SIMD operator",results},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"RampScalarSpline C# oracle PASS: {lanes} lanes, max error {maximum}");
    }
}
