using System.Text.Json;
using ValveResourceFormat.Particles;

sealed class ReplayEventTimeline
{
    private sealed record Literal(int Tick, int Point, float[] Position, float[] Rotation);
    private readonly List<Literal> literals = [];
    private readonly List<int> releases = [];
    private int? destroyTick;
    private bool destroyImmediately;
    public const string Policy = "agr_recorded_control_lifecycle_v1";

    public object[] LiteralReport => literals.Select(row => (object)new
    {
        tick = row.Tick, control_point = row.Point,
        position = row.Position.Select(value => (double)value).ToArray(),
        rotation = row.Rotation.Select(value => (double)value).ToArray(), interpolation_interval = 0
    }).ToArray();
    public object? DestroyReport => destroyTick.HasValue
        ? new { tick = destroyTick.Value, destroy_immediately = destroyImmediately } : null;
    public int[] ReleaseTicks => releases.ToArray();

    private static float[] Components(JsonElement row, string field, string components, float[] fallback)
    {
        if (!row.TryGetProperty(field, out var value)) return fallback;
        if (value.ValueKind != JsonValueKind.Object) throw new Exception("Invalid literal transform components");
        foreach (var property in value.EnumerateObject())
        {
            if (property.Name.Length != 1 || !components.Contains(property.Name[0]))
                throw new Exception("Unsupported literal transform component");
        }
        return components.Select(component => value.TryGetProperty(component.ToString(), out var number)
            ? number.GetSingle() : 0f).ToArray();
    }

    public static ReplayEventTimeline Parse(JsonElement instance, int birth, int end)
    {
        var result = new ReplayEventTimeline();
        if (!instance.TryGetProperty("events", out var events)) return result;
        var previousTick = birth;
        int? previousOrder = null;
        var creates = 0;
        var eventNumber = 0;
        JsonElement creation = default;
        foreach (var ev in events.EnumerateArray())
        {
            var kind = ev.GetProperty("type").GetString();
            var tick = ev.GetProperty("tick").GetInt32();
            if (tick < previousTick || tick > end) throw new Exception("Unordered particle event history");
            previousTick = tick;
            if (ev.TryGetProperty("order", out var orderInput))
            {
                var order = orderInput.GetInt32();
                if (previousOrder.HasValue && order <= previousOrder) throw new Exception("Particle event order is not strictly increasing");
                previousOrder = order;
            }
            if (kind == "CREATE")
            {
                if (eventNumber != 0 || tick != birth || ++creates != 1) throw new Exception("Invalid particle creation event");
            }
            else if (creates != 1) throw new Exception("Particle event precedes creation");
            eventNumber++;
            var data = ev.TryGetProperty("data", out var dataInput) ? dataInput : default;
            if (data.ValueKind == JsonValueKind.Object && data.TryGetProperty("index", out var eventIndex)
                && (!instance.TryGetProperty("index", out var instanceIndex) || eventIndex.GetInt32() != instanceIndex.GetInt32()))
                throw new Exception("Particle event index differs from its instance");
            switch (kind)
            {
                case "CREATE":
                    if (data.ValueKind == JsonValueKind.Object && data.TryGetProperty("create_particle", out var creationInput))
                        creation = creationInput;
                    break;
                case "UPDATE_ENT":
                    if (result.destroyTick.HasValue) throw new Exception("Attachment changes after particle destroy are unqualified");
                    break;
                case "UPDATE_TRANSFORM":
                    if (result.destroyTick.HasValue) throw new Exception("Literal changes after particle destroy are unqualified");
                    var transform = data.GetProperty("update_particle_transform");
                    foreach (var field in transform.EnumerateObject())
                    {
                        if (field.Name is not ("control_point" or "position" or "orientation" or "interpolation_interval"))
                            throw new Exception("Unsupported literal particle transform field");
                    }
                    var point = transform.GetProperty("control_point").GetInt32();
                    if (point < 0 || point > 63) throw new Exception("Invalid literal control point");
                    var interval = transform.TryGetProperty("interpolation_interval", out var intervalInput) ? intervalInput.GetSingle() : 0f;
                    if (interval != 0f) throw new Exception("Interpolated particle transforms are unqualified");
                    var position = Components(transform, "position", "xyz", [0, 0, 0]);
                    var rotation = Components(transform, "orientation", "xyzw", [0, 0, 0, 1]);
                    if (position.Any(value => !float.IsFinite(value)) || rotation.Any(value => !float.IsFinite(value))
                        || MathF.Abs(rotation.Sum(value => value * value) - 1) > 2e-5f)
                        throw new Exception("Invalid literal particle transform");
                    result.literals.Add(new(tick, point, position, rotation));
                    break;
                case "RELEASE":
                    result.releases.Add(tick);
                    break;
                case "DESTROY":
                    if (result.destroyTick.HasValue) throw new Exception("Repeated particle destroy event");
                    var destroy = data.GetProperty("destroy_particle");
                    foreach (var field in destroy.EnumerateObject())
                    {
                        if (field.Name != "destroy_immediately") throw new Exception("Unsupported particle destroy field");
                    }
                    result.destroyTick = tick;
                    result.destroyImmediately = destroy.TryGetProperty("destroy_immediately", out var immediate) && immediate.GetBoolean();
                    break;
                default:
                    throw new Exception($"Unsupported replay particle event {kind}; it cannot be ignored");
            }
        }
        if (eventNumber > 0 && creates != 1) throw new Exception("Missing particle creation event");
        if (instance.TryGetProperty("destroy_tick", out var summaryTick)
            && (summaryTick.ValueKind == JsonValueKind.Null ? result.destroyTick.HasValue : summaryTick.GetInt32() != result.destroyTick))
            throw new Exception("Particle destroy summary differs from events");
        if (result.destroyTick.HasValue && instance.TryGetProperty("destroy_immediately", out var summaryMode)
            && summaryMode.GetBoolean() != result.destroyImmediately)
            throw new Exception("Particle destroy mode differs from events");
        if (instance.TryGetProperty("release_ticks", out var summaryReleases)
            && !summaryReleases.EnumerateArray().Select(value => value.GetInt32()).SequenceEqual(result.releases))
            throw new Exception("Particle release summary differs from events");
        if (result.literals.Count > 0 || result.destroyTick.HasValue)
        {
            var index = instance.GetProperty("index").GetInt32();
            var generation = instance.GetProperty("generation").GetInt32();
            var handle = instance.GetProperty("entity_handle").GetUInt32();
            if (index < 0 || generation < 1 || handle == uint.MaxValue
                || instance.GetProperty("id").GetString() != $"{index}:{generation}"
                || instance.GetProperty("entity_id").GetString() != $"{handle & 0x3fff}:{handle >> 14}")
                throw new Exception("Recorded control/lifecycle requires exact particle generation and full entity identity");
            if (creation.ValueKind != JsonValueKind.Object
                || creation.GetProperty("entity_handle").GetUInt32() != handle
                || creation.GetProperty("attach_type").GetInt32() is not (2 or 5)
                || (creation.TryGetProperty("cluster", out var cluster) && cluster.GetBoolean()))
                throw new Exception("Recorded control/lifecycle requires a qualified entity-owned creation provider");
            if (instance.TryGetProperty("resource_hash", out var resourceHash)
                && creation.GetProperty("particle_name_index").GetString() != resourceHash.GetString())
                throw new Exception("Particle creation resource identity differs from its instance");
        }
        return result;
    }

    public void ValidateControls(Dictionary<int, JsonElement> controls, int birth, int end)
    {
        foreach (var group in literals.GroupBy(row => row.Point))
        {
            var updates = group.ToArray();
            if (updates[0].Tick != birth) throw new Exception("Literal control point lacks creation-time data");
            for (var tick = birth; tick <= end; tick++)
            {
                var expected = updates.Last(row => row.Tick <= tick);
                var snapshot = controls[tick];
                JsonElement position, rotation;
                if (group.Key == 0)
                {
                    position = snapshot.GetProperty("position");
                    rotation = snapshot.GetProperty("rotation");
                }
                else
                {
                    var points = snapshot.GetProperty("extra_control_points").EnumerateArray()
                        .Where(row => row.GetProperty("index").GetInt32() == group.Key).ToArray();
                    if (points.Length != 1) throw new Exception("Literal control point snapshot is missing or duplicated");
                    position = points[0].GetProperty("position");
                    rotation = points[0].GetProperty("rotation");
                }
                if (!position.EnumerateArray().Select(value => value.GetSingle()).SequenceEqual(expected.Position)
                    || !rotation.EnumerateArray().Select(value => value.GetSingle()).SequenceEqual(expected.Rotation))
                    throw new Exception("Supplied control snapshot differs from recorded literal transform");
            }
        }
    }

    public bool DestroyAt(int tick, ParticleSystemSimulation simulation)
        => destroyTick == tick && ReplayLifecycle.Destroy(simulation, destroyImmediately);

    public bool StartsEndCapAt(int tick) => destroyTick == tick && !destroyImmediately;
}
