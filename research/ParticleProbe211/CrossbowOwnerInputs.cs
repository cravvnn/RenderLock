#if AGR_SIMULATION_SNAPSHOT
using System.Text.Json;

/// <summary>Immutable current Crossbow owner/birth acknowledgement. The frontend
/// separately validates observed native acquisition, child graph and snapshot
/// poses. This is not a general CREATE3 alias or capture authentication.</summary>
static class CrossbowOwnerInputs
{
    public const string Policy = "observed_crossbow_owner_birth_6861_v1";
    public static JsonElement? Qualify(JsonElement instance)
    {
        if (!instance.TryGetProperty("crossbow_owner_provider", out var input)) return null;
        var fields = new HashSet<string>(["policy", "client_sha256", "particles_sha256", "entity_id", "resource", "created_tick",
            "model", "model_sha256", "owner_scale", "authored_matrix", "creation_mode", "observed_mode", "capture_sha256"], StringComparer.Ordinal);
        if (input.ValueKind != JsonValueKind.Object || input.EnumerateObject().Count() != fields.Count
            || input.EnumerateObject().Any(p => !fields.Contains(p.Name)))
            throw new InvalidDataException("Incomplete current Crossbow owner header");
        var handle = instance.GetProperty("entity_handle").GetUInt32();
        var entity = instance.GetProperty("entity_id").GetString();
        var birth = instance.GetProperty("created_tick").GetInt32();
        var capture = input.GetProperty("capture_sha256").GetString();
        const string model = "models/heroes_wip/priest/priest.vmdl_c";
        const string modelSha = "61d28824a8b99733e34164a7117ca84da91c82b7accc44432566798b7c99c432";
        if (input.GetProperty("policy").GetString() != Policy
            || input.GetProperty("client_sha256").GetString() != "d0341ce77398614af905ef8669d328fd4d45958282acef66789a250492e466b5"
            || input.GetProperty("particles_sha256").GetString() != "46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f"
            || instance.GetProperty("resource").GetString() != "particles/abilities/priest/priest_crossbow_buff.vpcf"
            || input.GetProperty("resource").GetString() != instance.GetProperty("resource").GetString()
            || handle is 0 or uint.MaxValue || entity != $"{handle & 0x3fff}:{handle >> 14}"
            || input.GetProperty("entity_id").GetString() != entity || input.GetProperty("created_tick").GetInt32() != birth
            || input.GetProperty("creation_mode").GetInt32() != 3 || input.GetProperty("observed_mode").GetInt32() != 1
            || input.GetProperty("owner_scale").GetSingle() != 1 || input.GetProperty("model").GetString() != model
            || input.GetProperty("model_sha256").GetString() != modelSha || capture is not { Length: 64 }
            || !capture.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f'))
            throw new InvalidDataException("Unqualified current Crossbow owner identity");
        var matrix = input.GetProperty("authored_matrix");
        if (matrix.ValueKind != JsonValueKind.Array || matrix.GetArrayLength() != 12)
            throw new InvalidDataException("Crossbow requires a flat identity Source matrix");
        var n = 0;
        foreach (var value in matrix.EnumerateArray())
        {
            if (value.GetSingle() != (n is 0 or 5 or 10 ? 1 : 0))
                throw new InvalidDataException("Crossbow requires identity authored CP0");
            n++;
        }
        var models = instance.GetProperty("snapshot_model_bindings");
        if (instance.GetProperty("snapshot_model_policy").GetString() != SnapshotRuntimeInputs.Policy
            || models.ValueKind != JsonValueKind.Array || models.GetArrayLength() != 1)
            throw new InvalidDataException("Crossbow requires an independent CP0 snapshot provider");
        var binding = models[0];
        if (binding.GetProperty("control_point").GetInt32() != 0 || binding.GetProperty("bone").GetString() != "crossbow"
            || binding.GetProperty("entity_id").GetString() != entity || binding.GetProperty("model").GetString() != model
            || binding.GetProperty("model_sha256").GetString() != modelSha)
            throw new InvalidDataException("Crossbow snapshot model/owner differs");
        var events = instance.GetProperty("events");
        if (events.ValueKind != JsonValueKind.Array || events.GetArrayLength() is 0 or > 100000)
            throw new InvalidDataException("Crossbow requires bounded original event history");
        var first = events[0];
        if (first.GetProperty("type").GetString() != "CREATE" || first.GetProperty("tick").GetInt32() != birth)
            throw new InvalidDataException("Crossbow creation is not first at birth");
        var create = first.GetProperty("data").GetProperty("create_particle");
        if (create.GetProperty("attach_type").GetInt32() != 3 || create.GetProperty("entity_handle").GetUInt32() != handle
            || create.EnumerateObject().Any(p => p.Name is not ("attach_type" or "entity_handle" or "cluster" or "particle_name_index"))
            || (create.TryGetProperty("cluster", out var cluster) && cluster.GetBoolean())
            || create.GetProperty("particle_name_index").GetString() != instance.GetProperty("resource_hash").GetString())
            throw new InvalidDataException("Crossbow cannot rewrite another creation mode or resource");
        var points = new HashSet<int>();
        var literalBirth = false;
        foreach (var ev in events.EnumerateArray())
        {
            var kind = ev.GetProperty("type").GetString();
            if (kind == "UPDATE_ENT")
            {
                var b = ev.GetProperty("data").GetProperty("update_particle_ent");
                var cp = b.GetProperty("control_point").GetInt32();
                var mode = b.GetProperty("attach_type").GetInt32();
                var attachment = b.GetProperty("attachment").GetInt32();
                if (cp is < 0 or > 2 || !points.Add(cp) || ev.GetProperty("tick").GetInt32() != birth
                    || b.GetProperty("entity_handle").GetUInt32() != handle
                    || (cp == 0 ? mode != 1 || attachment != 0 : mode != 5 || attachment <= 0)
                    || b.EnumerateObject().Any(p => p.Name is not ("control_point" or "entity_handle" or "attach_type"
                        or "attachment" or "include_wearables" or "offset_position" or "offset_angles" or "fallback_position")))
                    throw new InvalidDataException("Crossbow requires unique own birth CP0/1/2");
                if (b.TryGetProperty("include_wearables", out var wearables)
                    && wearables.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                    throw new InvalidDataException("Invalid Crossbow wearable flag");
                foreach (var name in new[] { "offset_position", "offset_angles", "fallback_position" })
                {
                    if (!b.TryGetProperty(name, out var values)) continue;
                    if (values.ValueKind != JsonValueKind.Object || values.EnumerateObject().Any(p =>
                        p.Name.Length != 1 || !(name == "offset_position" ? "xyzw" : "xyz").Contains(p.Name[0])
                        || !float.IsFinite(p.Value.GetSingle()) || ((name != "fallback_position" || cp == 0) && p.Value.GetSingle() != 0)))
                        throw new InvalidDataException("Unqualified Crossbow offsets/fallback");
                }
            }
            if (kind == "UPDATE_TRANSFORM")
            {
                var cp = ev.GetProperty("data").GetProperty("update_particle_transform").GetProperty("control_point").GetInt32();
                if (cp != 5) throw new InvalidDataException("Crossbow literal cannot replace an entity provider");
                literalBirth |= ev.GetProperty("tick").GetInt32() == birth;
            }
        }
        if (points.Count != 3 || !literalBirth) throw new InvalidDataException("Crossbow control providers are incomplete");
        return input.Clone();
    }
}
#endif
