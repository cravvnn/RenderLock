#if AGR_SIMULATION_HITBOX
using System.Text.Json;

/// <summary>Separate finite-fallback Silence birth admission. Flags0 means the
/// observed mode1 selects the unit owner transform; original CREATE stays3.</summary>
static class SilenceBirthInputs
{
    public const string Policy="observed_silence_birth_fallback_v1";
    public static JsonElement? Qualify(JsonElement instance)
    {
        if(!instance.TryGetProperty("silence_birth_provider",out var input))return null;
        if(new[]{"custom_origin_provider","birth_override_provider","crossbow_owner_provider",
            "world_origin_provider","snapshot_model_policy","snapshot_model_bindings"}.Any(k=>instance.TryGetProperty(k,out _)))
            throw new InvalidDataException("Silence cannot combine creation/world/snapshot providers");
        static bool SHA(string? s)=>s is {Length:64}&&s.All(c=>c is >= '0' and <= '9' or >= 'a' and <= 'f');
        var fields=new HashSet<string>(["policy","client_sha256","particles_sha256","entity_id","resource","created_tick","model","model_sha256",
            "owner_scale","identity_flags","authored_matrix","fallback_position","observed_fallback_position","creation_mode","observed_mode","capture_sha256"],StringComparer.Ordinal);
        if(input.ValueKind!=JsonValueKind.Object||input.EnumerateObject().Count()!=fields.Count||input.EnumerateObject().Any(p=>!fields.Contains(p.Name)))
            throw new InvalidDataException("Incomplete Silence birth header");
        var entity=instance.GetProperty("entity_id").GetString();var handle=instance.GetProperty("entity_handle").GetUInt32();
        var birth=instance.GetProperty("created_tick").GetInt32();
        if(input.GetProperty("policy").GetString()!=Policy
            ||input.GetProperty("client_sha256").GetString()!="2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7"
            ||input.GetProperty("particles_sha256").GetString()!="5d5e29757162d86a4bb940c0f0b0189fbf12598d4238a7d27ec4082e9740ff7b"
            ||instance.GetProperty("resource").GetString()!="particles/modifiers/silenced_debuff.vpcf"
            ||input.GetProperty("resource").GetString()!=instance.GetProperty("resource").GetString()
            ||handle is 0 or uint.MaxValue||entity!=$"{handle&0x3fff}:{handle>>14}"||input.GetProperty("entity_id").GetString()!=entity
            ||input.GetProperty("created_tick").GetInt32()!=birth||input.GetProperty("creation_mode").GetInt32()!=3
            ||input.GetProperty("observed_mode").GetInt32()!=1||input.GetProperty("owner_scale").GetSingle()!=1
            ||input.GetProperty("identity_flags").GetUInt32()!=0||input.GetProperty("model").GetString()!="models/heroes_wip/priest/priest.vmdl_c"
            ||!SHA(input.GetProperty("model_sha256").GetString())||!SHA(input.GetProperty("capture_sha256").GetString()))
            throw new InvalidDataException("Unqualified Silence birth identity");
        var matrix=input.GetProperty("authored_matrix").EnumerateArray().Select(r=>r.EnumerateArray().Select(n=>n.GetSingle()).ToArray()).ToArray();
        if(matrix.Length!=3||matrix.Any(r=>r.Length!=4)||matrix.Where((r,i)=>r.Where((v,j)=>v!=(i==j?1:0)).Any()).Any())
            throw new InvalidDataException("Silence birth requires identity local matrix");
        var fallback=input.GetProperty("fallback_position").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
        if(fallback.Length!=3||fallback.Any(v=>!float.IsFinite(v)||MathF.Abs(v)>=3e38f))
            throw new InvalidDataException("Silence requires an explicit finite position fallback");
        var observed=input.GetProperty("observed_fallback_position").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
        if(observed.Length!=3||observed.Any(v=>!float.IsFinite(v)||MathF.Abs(v)>=3e38f))
            throw new InvalidDataException("Silence requires its distinct finite observed fallback");
        var models=instance.GetProperty("model_hitbox_bindings").EnumerateArray().ToArray();
        if(instance.GetProperty("model_hitbox_policy").GetString()!=HitboxRuntimeInputs.Policy||models.Length!=1
            ||models[0].GetProperty("control_point").GetInt32()!=0||models[0].GetProperty("entity_id").GetString()!=entity
            ||models[0].GetProperty("model").GetString()!=input.GetProperty("model").GetString()
            ||models[0].GetProperty("model_sha256").GetString()!=input.GetProperty("model_sha256").GetString())
            throw new InvalidDataException("Silence birth requires its separate exact CP0 hitbox model binding");
        var events=instance.GetProperty("events").EnumerateArray().ToArray();
        if(events.Length==0||events[0].GetProperty("type").GetString()!="CREATE")throw new InvalidDataException("Silence requires unchanged creation history");
        var creation=events[0].GetProperty("data").GetProperty("create_particle");
        if(creation.GetProperty("attach_type").GetInt32()!=3||creation.GetProperty("entity_handle").GetUInt32()!=handle
            ||creation.EnumerateObject().Any(p=>p.Name is not("attach_type" or "entity_handle" or "cluster" or "particle_name_index"))
            ||(creation.TryGetProperty("cluster",out var cluster)&&cluster.GetBoolean()))
            throw new InvalidDataException("Silence cannot reinterpret other creation modes or local overrides");
        var cp0=events.Where(e=>e.GetProperty("type").GetString()=="UPDATE_ENT"
            &&e.GetProperty("data").GetProperty("update_particle_ent").GetProperty("control_point").GetInt32()==0).ToArray();
        if(cp0.Length!=1||cp0[0].GetProperty("tick").GetInt32()!=birth)throw new InvalidDataException("Silence requires exactly one birth-time CP0 update");
        var binding=cp0[0].GetProperty("data").GetProperty("update_particle_ent");
        if(binding.GetProperty("entity_handle").GetUInt32()!=handle||binding.GetProperty("attach_type").GetInt32()!=1
            ||binding.GetProperty("attachment").GetInt32()!=0||binding.EnumerateObject().Any(p=>p.Name is not
                ("control_point" or "entity_handle" or "attach_type" or "attachment" or "include_wearables" or "offset_position" or "offset_angles" or "fallback_position")))
            throw new InvalidDataException("Silence requires its exact unnamed owner CP0 mode1");
        if(binding.TryGetProperty("include_wearables",out var wearables)&&wearables.ValueKind is not(JsonValueKind.True or JsonValueKind.False))
            throw new InvalidDataException("Invalid Silence wearable flag");
        foreach(var name in new[]{"offset_position","offset_angles"})
            if(binding.TryGetProperty(name,out var value)&&(value.ValueKind!=JsonValueKind.Object||value.EnumerateObject().Any(p=>
                p.Name.Length!=1||!(name=="offset_position"?"xyzw":"xyz").Contains(p.Name[0])||p.Value.GetSingle()!=0)))
                throw new InvalidDataException("Silence cannot qualify CP0 local offsets");
        var recorded=binding.GetProperty("fallback_position");
        if(recorded.ValueKind!=JsonValueKind.Object||recorded.EnumerateObject().Count()!=3
            ||recorded.EnumerateObject().Any(p=>p.Name is not("x" or "y" or "z"))
            ||!new[]{"x","y","z"}.Select(k=>recorded.GetProperty(k).GetSingle()).SequenceEqual(fallback))
            throw new InvalidDataException("Silence fallback differs from its observed birth contract");
        if(events.Any(e=>e.GetProperty("type").GetString()=="UPDATE_TRANSFORM"
            &&e.GetProperty("data").GetProperty("update_particle_transform").GetProperty("control_point").GetInt32()==0))
            throw new InvalidDataException("Silence cannot qualify literal CP0 replacement");
        return input.Clone();
    }
}

#endif
