#if AGR_SIMULATION_SNAPSHOT
using System.Globalization;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ValveResourceFormat.Particles;
using ValveResourceFormat.Particles.Utils;

/// <summary>Explicit named-bone matrices; acquisition and creation admission are separate.</summary>
sealed class SnapshotRuntimeInputs
{
    public const string Policy="agr_explicit_snapshot_bone_frames_v1";
    private readonly Dictionary<int,Dictionary<(int,string),SnapshotModelBinding>> frames=new();
    private readonly Dictionary<(int,string),string> bindings=new();
    private readonly HashSet<string> queried=new(StringComparer.Ordinal);
    private readonly int birth,end;
    private int active;
    public JsonElement Bindings { get; }

    private SnapshotRuntimeInputs(JsonElement instance,Dictionary<int,JsonElement> controls,int birth,int end)
    {
        this.birth=birth;this.end=end;active=birth-1;
        if(birth<0||end<birth||end-birth>=100000||instance.GetProperty("snapshot_model_policy").GetString()!=Policy)
            throw new InvalidDataException("Unqualified snapshot model history/policy");
        Bindings=instance.GetProperty("snapshot_model_bindings").Clone();
        foreach(var row in Bindings.EnumerateArray())
        {
            Fields(row,["control_point","entity_id","model","model_sha256","bone_order_sha256","bone","binding_id"]);
            var cp=row.GetProperty("control_point").GetInt32();var bone=row.GetProperty("bone").GetString();
            var entity=row.GetProperty("entity_id").GetString();var model=row.GetProperty("model").GetString();
            var sha=row.GetProperty("model_sha256").GetString();var order=row.GetProperty("bone_order_sha256").GetString();
            var id=row.GetProperty("binding_id").GetString();
            if(cp is <0 or >63||string.IsNullOrWhiteSpace(bone)||bone.Length>256||bone.Any(c=>c is <' ' or >'~')
                ||string.IsNullOrEmpty(entity)||entity.Split(':').Length!=2
                ||entity.Split(':').Any(s=>s.Length==0||s.Any(c=>c is <'0' or >'9'))
                ||model is null||model.Length>512||!model.StartsWith("models/",StringComparison.Ordinal)||!model.EndsWith(".vmdl_c",StringComparison.Ordinal)
                ||model.Contains("..",StringComparison.Ordinal)||model.Contains(':')||model.Contains('\\')||model.Any(c=>c is <' ' or >'~')
                ||!SHA(sha)||!SHA(order)||!SHA(id)||bindings.Count>=256||!bindings.TryAdd((cp,bone),id!))
                throw new InvalidDataException("Invalid/duplicate snapshot model binding");
            var canonical=string.Join('\n',cp.ToString(CultureInfo.InvariantCulture),entity,model,sha,order,bone);
            if(Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)))!=id)
                throw new InvalidDataException("Snapshot model binding hash differs from its identity");
        }
        if(bindings.Count==0)throw new InvalidDataException("Empty snapshot model bindings");
        long total=0;
        for(int tick=birth;tick<=end;tick++)
        {
            if(!controls.TryGetValue(tick,out var control)||!control.TryGetProperty("snapshot_model_bones",out var rows))
                throw new InvalidDataException("Missing snapshot model frame");
            var entries=new Dictionary<(int,string),SnapshotModelBinding>();
            foreach(var row in rows.EnumerateArray())
            {
                Fields(row,["control_point","bone","binding_id","matrix"]);
                var cp=row.GetProperty("control_point").GetInt32();var bone=row.GetProperty("bone").GetString();
                if(bone is null||!bindings.TryGetValue((cp,bone),out var id)||row.GetProperty("binding_id").GetString()!=id)
                    throw new InvalidDataException("Snapshot model frame changed its CP/bone/model identity");
                var matrixInput=row.GetProperty("matrix");
                if(matrixInput.ValueKind!=JsonValueKind.Array||matrixInput.GetArrayLength()!=12)
                    throw new InvalidDataException("Snapshot matrix requires exactly twelve scalar values");
                var values=matrixInput.EnumerateArray().Select(x=>x.GetSingle()).ToArray();
                var matrix=new SnapshotSkinningMatrix(values);
                var a=new Vector3(values[0],values[1],values[2]);var b=new Vector3(values[4],values[5],values[6]);var c=new Vector3(values[8],values[9],values[10]);
                var gram=new[]{a.LengthSquared()-1,b.LengthSquared()-1,c.LengthSquared()-1,Vector3.Dot(a,b),Vector3.Dot(a,c),Vector3.Dot(b,c)};
                if(gram.Any(v=>!float.IsFinite(v)||MathF.Abs(v)>2e-4f)||MathF.Abs(Vector3.Dot(a,Vector3.Cross(b,c))-1)>2e-4f
                    ||!entries.TryAdd((cp,bone),new(id,matrix))||++total>2000000)
                    throw new InvalidDataException("Snapshot matrices require finite rigid transforms and unique bounded bone rows");
            }
            if(entries.Count!=bindings.Count)throw new InvalidDataException("Incomplete snapshot bone frame");
            frames.Add(tick,entries);
        }
    }

    static bool SHA(string? s)=>s is {Length:64}&&s.All(c=>c is >='0' and <='9' or >='a' and <='f');
    static void Fields(JsonElement row,string[] expected)
    {
        if(row.ValueKind!=JsonValueKind.Object)throw new InvalidDataException("Snapshot model row must be an object");
        var names=row.EnumerateObject().Select(p=>p.Name).ToArray();
        if(names.Length!=expected.Length||names.Distinct(StringComparer.Ordinal).Count()!=expected.Length
            ||names.Any(n=>!expected.Contains(n,StringComparer.Ordinal)))throw new InvalidDataException("Snapshot model row fields changed");
    }
    public static SnapshotRuntimeInputs? Parse(JsonElement instance,Dictionary<int,JsonElement> controls,int birth,int end)
    {
        bool declared=instance.TryGetProperty("snapshot_model_policy",out _),any=controls.Values.Any(c=>c.TryGetProperty("snapshot_model_bones",out _));
        if(!declared&&!any&&!instance.TryGetProperty("snapshot_model_bindings",out _))return null;
        if(!declared||!any)throw new InvalidDataException("Incomplete snapshot model request contract");
        return new(instance,controls,birth,end);
    }
    public void Advance(int tick)
    {
        if(tick!=active+1||!frames.ContainsKey(tick))throw new InvalidDataException("Snapshot model clock must advance exactly one frame");
        active=tick;
    }
    public SnapshotModelBinding Query(int cp,string bone)
    {
        if(active<birth||!frames[active].TryGetValue((cp,bone),out var value))
            throw new InvalidDataException("Snapshot bone has no explicit active frame");
        queried.Add(value.Identity);
        return value;
    }
    public string[] Complete()
    {
        if(active!=end||!queried.SetEquals(bindings.Values))throw new InvalidDataException("Snapshot model input was not completely advanced/queried");
        return queried.Order(StringComparer.Ordinal).ToArray();
    }
}
#endif
