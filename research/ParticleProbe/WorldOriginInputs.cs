using System.Text.Json;

/// <summary>Separate unowned literal-birth/release profile; no owned stop behavior.
/// Whole request SHA and Python source/package checks bind the replay identity.
/// This admits no DESTROY, entity binding, later literal change or other resource.</summary>
static class WorldOriginInputs
{
    public const string Policy = "agr_world_literal_birth_release_v1";
    public const string Resource = "particles/abilities/vampirebat/vampirebat_batswarm_explosion.vpcf";
    public const string ClientHash = "2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7";
    private const string ResourceHash = "6452708104859076553";

    private static void Require(bool value, string reason)
    {
        if (!value) throw new Exception("World-origin particles: " + reason);
    }
    private static bool Fields(JsonElement row, params string[] names)
        => row.ValueKind == JsonValueKind.Object
            && row.EnumerateObject().Select(p => p.Name).Order().SequenceEqual(names.Order());
    private static bool Sha(JsonElement value)
        => value.ValueKind == JsonValueKind.String && value.GetString() is string s
            && s.Length == 64 && s.All(c => "0123456789abcdef".Contains(c));

    public static JsonElement? Qualify(JsonElement instance, int birth, int end, int? start, int? rate)
    {
        if (!instance.TryGetProperty("world_origin_provider", out var header)) return null;
        Require(start.HasValue && rate == 64 && start >= 0 && start <= birth && birth <= end && end - birth <= 4095,
            "bounded in-range birth and actual 64Hz output are required");
        Require(Fields(header, "policy", "client_sha256", "instance_id", "resource", "created_tick", "start_tick",
            "end_tick", "tick_rate", "demo_sha256", "events_sha256"), "unknown or incomplete provider fields");
        Require(header.GetProperty("policy").GetString() == Policy && header.GetProperty("client_sha256").GetString() == ClientHash
            && header.GetProperty("resource").GetString() == Resource && instance.GetProperty("resource").GetString() == Resource
            && header.GetProperty("created_tick").GetInt32() == birth && header.GetProperty("start_tick").GetInt32() == start
            && header.GetProperty("end_tick").GetInt32() == end && header.GetProperty("tick_rate").GetInt32() == rate
            && Sha(header.GetProperty("demo_sha256")) && Sha(header.GetProperty("events_sha256")), "provider identity or timing differs");
        var index = instance.GetProperty("index").GetInt32();
        var generation = instance.GetProperty("generation").GetInt32();
        Require(index >= 0 && generation >= 1 && instance.GetProperty("id").GetString() == $"{index}:{generation}"
            && header.GetProperty("instance_id").GetString() == instance.GetProperty("id").GetString()
            && instance.GetProperty("entity_id").ValueKind == JsonValueKind.Null
            && instance.GetProperty("entity_handle").GetUInt32() == 0xffffff
            && instance.GetProperty("selection_scope").GetString() == "unattached_custom_origin"
            && instance.GetProperty("involved_entity_ids").GetArrayLength() == 0
            && instance.GetProperty("involved_entity_handles").GetArrayLength() == 0,
            "unowned generation/selection identity differs");
        foreach (var key in new[] { "custom_origin_provider", "birth_override_provider", "silence_birth_provider",
            "laser_static_provider", "model_hitbox_policy", "model_hitbox_bindings", "snapshot_model_policy", "snapshot_model_bindings" })
            Require(!instance.TryGetProperty(key, out _), "world and entity/model providers cannot be combined");
        var events = instance.GetProperty("events").EnumerateArray().ToArray();
        Require(events.Length == 3, "requires three complete birth-time events");
        var kinds = new[] { "CREATE", "UPDATE_TRANSFORM", "RELEASE" };
        var firstOrder = events[0].GetProperty("order").GetInt32();
        Require(firstOrder >= 0 && firstOrder <= int.MaxValue - 2, "source event order exceeds bounds");
        for (var i = 0; i < events.Length; ++i)
        {
            Require(events[i].GetProperty("type").GetString() == kinds[i] && events[i].GetProperty("tick").GetInt32() == birth
                && (long)events[i].GetProperty("order").GetInt32() == (long)firstOrder + i, "event type/time/order differs");
            var data = events[i].GetProperty("data");
            Require(data.GetProperty("type").GetString() == "GAME_PARTICLE_MANAGER_EVENT_" + kinds[i]
                && data.GetProperty("index").GetInt32() == index, "recorded event identity differs");
            if (i == 0) Require(Fields(data, "type", "index", "create_particle"), "creation has unknown fields");
            if (i == 1) Require(Fields(data, "type", "index", "update_particle_transform"), "transform has unknown fields");
            if (i == 2) Require(Fields(data, "type", "index") || (Fields(data, "type", "index", "release_particle_index")
                && Fields(data.GetProperty("release_particle_index"))), "release has unknown fields");
        }
        var creation = events[0].GetProperty("data").GetProperty("create_particle");
        var summary = instance.GetProperty("create");
        foreach (var row in new[] { creation, summary })
            Require(Fields(row, "particle_name_index", "attach_type", "entity_handle", "cluster")
                && row.GetProperty("particle_name_index").GetString() == ResourceHash && row.GetProperty("attach_type").GetInt32() == 8
                && row.GetProperty("entity_handle").GetUInt32() == 0xffffff && !row.GetProperty("cluster").GetBoolean(),
                "creation fields differ from the unowned mode8 profile");
        Require(instance.GetProperty("resource_hash").GetString() == ResourceHash, "resource hash differs");
        var transform = events[1].GetProperty("data").GetProperty("update_particle_transform");
        Require(transform.GetProperty("control_point").GetInt32() == 0 && Fields(transform.GetProperty("position"), "x", "y", "z"),
            "explicit CP0 position is required");
        Require(new[] { "x", "y", "z" }.All(c => float.IsFinite(transform.GetProperty("position").GetProperty(c).GetSingle())
            && MathF.Abs(transform.GetProperty("position").GetProperty(c).GetSingle()) <= 131072), "CP0 position exceeds bounds");
        if (transform.TryGetProperty("orientation", out var rotation))
            Require(Fields(rotation, "x", "y", "z", "w") && rotation.GetProperty("x").GetSingle() == 0
                && rotation.GetProperty("y").GetSingle() == 0 && rotation.GetProperty("z").GetSingle() == 0
                && rotation.GetProperty("w").GetSingle() == 1, "world birth requires identity orientation");
        var controls = instance.GetProperty("control_points").EnumerateArray().ToArray();
        Require(controls.Length == end - birth + 1 && controls.All(c => Fields(c, "tick", "position", "rotation")),
            "world controls must be explicit literal snapshots only");
        return header.Clone();
    }
}
