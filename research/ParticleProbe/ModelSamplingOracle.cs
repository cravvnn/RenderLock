using System.Numerics;
using System.Text.Json;
using ValveResourceFormat.Particles;

static class ModelSamplingOracle
{
    public static void Check(string input, string output)
    {
        using var document=JsonDocument.Parse(File.ReadAllText(input));
        static float[] A(JsonElement value)=>value.EnumerateArray().Select(x=>x.GetSingle()).ToArray();
        static Vector3 V(JsonElement value){var a=A(value);return new(a[0],a[1],a[2]);}
        var maximum=0f;var particles=0;var cases=0;
        foreach(var row in document.RootElement.GetProperty("cases").EnumerateArray())
        {
            var bones=row.GetProperty("bones").EnumerateArray().Select(x=>{
                var v=A(x);return new ModelBonePose(new(v[0],v[1],v[2]),new(v[4],v[5],v[6],v[7]),v[3]);
            }).ToArray();
            var draws=row.GetProperty("draws").EnumerateArray().ToArray();var cursor=0;
            float Draw(bool extra){var d=draws[cursor++];if(d[0].GetBoolean()!=extra)throw new Exception("RNG draw type mismatch");return d[1].GetSingle();}
            foreach(var expected in row.GetProperty("expected").EnumerateArray())
            {
                var sample=ModelBoneSampling.Sample(bones,V(row.GetProperty("origin")),V(row.GetProperty("scale")),V(row.GetProperty("bias")),Draw);
                maximum=MathF.Max(maximum,Vector3.Abs(sample.Position-V(expected.GetProperty("position"))).MaxComponent());
                maximum=MathF.Max(maximum,Vector3.Abs(sample.Coordinates-V(expected.GetProperty("coordinates"))).MaxComponent());
                if(sample.Index!=expected.GetProperty("index").GetInt32())throw new Exception("Model shape index mismatch");
                particles++;
            }
            if(cursor!=draws.Length)throw new Exception("RNG consumption mismatch");
            cases++;
        }
        if(maximum>=.0001f)throw new Exception("Model sampling difference: "+maximum);
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",cases,particles,maximum,scope="Prepared C# bone-box sampler vs Python/native-checked fixtures; no runtime binding"}));
        Console.WriteLine($"PASS {cases} cases/{particles} particles; max {maximum}");
    }
    private static float MaxComponent(this Vector3 v)=>MathF.Max(v.X,MathF.Max(v.Y,v.Z));
}
