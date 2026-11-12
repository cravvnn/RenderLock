#if AGR_SIMULATION_HITBOX
using System.Collections.Immutable;
using System.Numerics;
using System.Text.Json;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using ValveResourceFormat.Particles;

/// <summary>Explicit request transport; no entity/model acquisition or CP alias.</summary>
sealed class HitboxRuntimeInputs
{
    public const string Policy = "agr_explicit_cp_hitbox_frames_v1";
    private sealed record Input(string Binding, Dictionary<string, ImmutableArray<AuthoredHitboxShape>> Sets);
    private readonly Dictionary<int, Dictionary<int, Input>> frames = new();
    private readonly Dictionary<ParticleSystemState, HitboxFrameHistory> histories = new();
    private int active;
    public JsonElement Bindings { get; }

    private HitboxRuntimeInputs(JsonElement instance, Dictionary<int, JsonElement> controls, int first, int last)
    {
        active=first-1;
        if(first<0||last<first||last==int.MaxValue||last-first>=10000||instance.GetProperty("model_hitbox_policy").GetString()!=Policy)
            throw new InvalidDataException("Unsupported explicit hitbox request");
        Bindings=instance.GetProperty("model_hitbox_bindings").Clone();
        var bindings=new Dictionary<int,string>();
        static bool SHA(string? s)=>s is {Length:64}&&s.All(c=>c is >= '0' and <= '9' or >= 'a' and <= 'f');
        foreach(var row in Bindings.EnumerateArray())
        {
            var cp=row.GetProperty("control_point").GetInt32();var id=row.GetProperty("binding_id").GetString();
            var entity=row.GetProperty("entity_id").GetString();
            if(cp<0||cp>63||!SHA(id)||!SHA(row.GetProperty("model_sha256").GetString())
                ||!SHA(row.GetProperty("bone_order_sha256").GetString())
                ||string.IsNullOrWhiteSpace(entity)||entity.Split(':').Length!=2
                ||entity.Split(':').Any(s=>s.Length==0||s.Any(c=>c is < '0' or > '9'))
                ||row.GetProperty("model").GetString() is not { } model||!model.EndsWith(".vmdl_c",StringComparison.Ordinal)
                ||model.StartsWith('/')||model.Contains("..",StringComparison.Ordinal)||model.Contains(':')||model.Contains('\\')
                ||model.Any(c=>c is < ' ' or > '~')
                ||!bindings.TryAdd(cp,id!))throw new InvalidDataException("Invalid hitbox binding header");
            var canonical=string.Join('\n',cp.ToString(CultureInfo.InvariantCulture),entity,model,
                row.GetProperty("model_sha256").GetString(),row.GetProperty("bone_order_sha256").GetString());
            if(Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)))!=id)
                throw new InvalidDataException("Hitbox binding header hash mismatch");
        }
        if(bindings.Count==0)throw new InvalidDataException("Empty hitbox bindings");
        var layout=new Dictionary<(int,string),ImmutableArray<(Vector3,Vector3)>>();long total=0;
        for(int tick=first;tick<=last;tick++)
        {
            if(!controls.TryGetValue(tick,out var control)||!control.TryGetProperty("model_hitboxes",out var rows))
                throw new InvalidDataException("Missing explicit hitbox frame");
            var entries=new Dictionary<int,Input>();
            foreach(var row in rows.EnumerateArray())
            {
                var cp=row.GetProperty("control_point").GetInt32();
                if(!bindings.TryGetValue(cp,out var identity)||row.GetProperty("binding_id").GetString()!=identity)
                    throw new InvalidDataException("Hitbox binding changed between frames");
                if(entries.ContainsKey(cp))throw new InvalidDataException("Duplicate hitbox CP");
                var sets=new Dictionary<string,ImmutableArray<AuthoredHitboxShape>>(StringComparer.Ordinal);
                foreach(var set in row.GetProperty("sets").EnumerateObject())
                {
                    if(set.Name.Length>127||sets.Count>=128)throw new InvalidDataException("Hitbox set limits");
                    var count=set.Value.GetArrayLength();
                    if(count is <1 or >4096||total+count>200000)
                        throw new InvalidDataException("Hitbox shape limits");
                    var shapes=set.Value.EnumerateArray().Select(ParseShape).ToImmutableArray();
                    total+=shapes.Length;
                    if(shapes.IsEmpty||shapes.Length>4096||total>200000||!sets.TryAdd(set.Name,shapes))
                        throw new InvalidDataException("Hitbox shape limits or duplicate set");
                    var bounds=shapes.Select(s=>(s.Min,s.Max)).ToImmutableArray();var key=(cp,set.Name);
                    if(tick==first)layout.Add(key,bounds);
                    else if(!layout.TryGetValue(key,out var original)||!original.SequenceEqual(bounds))
                        throw new InvalidDataException("Authored hitbox layout changed");
                }
                if(sets.Count==0||!entries.TryAdd(cp,new(identity,sets)))throw new InvalidDataException("Empty or duplicate hitbox CP");
            }
            if(entries.Count!=bindings.Count||entries.Any(e=>layout.Keys.Count(k=>k.Item1==e.Key)!=e.Value.Sets.Count))
                throw new InvalidDataException("Incomplete hitbox CP/set history");
            frames.Add(tick,entries);
        }
    }

    public static HitboxRuntimeInputs? Parse(JsonElement instance,Dictionary<int,JsonElement> controls,int birth,int end)
    {
        var declared=instance.TryGetProperty("model_hitbox_policy",out _);
        var any=controls.Values.Any(c=>c.TryGetProperty("model_hitboxes",out _));
        if(!declared&&!any&&!instance.TryGetProperty("model_hitbox_bindings",out _))return null;
        if(!declared||!any)throw new InvalidDataException("Incomplete hitbox request contract");
        return new(instance,controls,birth,end);
    }

    public void Advance(int tick)
    {
        if(tick!=active+1||!frames.ContainsKey(tick))throw new InvalidDataException("Hitbox request clock must advance exactly one frame");
        active=tick;
    }

    public HitboxFrameHistory.Frame Query(ParticleSystemState consumer,int cp,string set,bool weighted)
    {
        if(!frames.TryGetValue(active,out var frame))throw new InvalidDataException("Hitbox frame has not been installed");
        if(!frame.TryGetValue(cp,out var input))throw new InvalidDataException("Missing explicit hitbox control point");
        if(!input.Sets.TryGetValue(set,out var shapes)&&!input.Sets.TryGetValue("default",out shapes))
            throw new InvalidDataException("Requested/default hitbox set missing");
        if(weighted)shapes=shapes.Select(s=>s with {Weight=((s.Max.Y-s.Min.Y)*(s.Max.X-s.Min.X))*(s.Max.Z-s.Min.Z)}).ToImmutableArray();
        if(!histories.TryGetValue(consumer,out var history))
        {
            if(histories.Count>=256)throw new InvalidDataException("Hitbox consumer context limit");
            history=new();histories.Add(consumer,history);
        }
        return history.Query(cp,consumer.Age,input.Binding,shapes);
    }

    private static AuthoredHitboxShape ParseShape(JsonElement row)
    {
        static float[] A(JsonElement value,int n){var a=value.EnumerateArray().Select(x=>x.GetSingle()).ToArray();if(a.Length!=n||a.Any(x=>!float.IsFinite(x)))throw new InvalidDataException("Invalid hitbox vector");return a;}
        var low=A(row.GetProperty("min"),3);var high=A(row.GetProperty("max"),3);var pose=A(row.GetProperty("transform"),8);
        var q=new Quaternion(pose[4],pose[5],pose[6],pose[7]);
        if(low.Where((v,i)=>v>high[i]).Any()||pose[3]<=0||!float.IsFinite(q.LengthSquared())||MathF.Abs(q.LengthSquared()-1)>2e-4f)
            throw new InvalidDataException("Unqualified authored hitbox bounds or pose");
        return new(new(low[0],low[1],low[2]),new(high[0],high[1],high[2]),new(pose[0],pose[1],pose[2]),q,pose[3],1,-1);
    }
}

#endif
