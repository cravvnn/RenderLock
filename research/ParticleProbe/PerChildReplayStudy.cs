using System.Numerics;
using System.Security.Cryptography;
using System.Text.Json;
using ValveKeyValue;
using ValveResourceFormat.IO;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

static class PerChildReplayStudy
{
    const string Source = "particles/generic/sprint_pnt.vpcf";
    public const string RootName = "particles/agr_study/controlled_sprint_per_child.vpcf";

    public static void Run(string request, string output)
    {
        if (File.Exists(output)) throw new Exception("Choose a new study output");
        var bytes = File.ReadAllBytes(request);
        using var document = JsonDocument.Parse(bytes);
        var config = document.RootElement;
        var start = config.GetProperty("start_tick").GetInt32();
        var end = config.GetProperty("end_tick").GetInt32();
        var rate = config.GetProperty("tick_rate").GetInt32();
        var outputStart = config.TryGetProperty("scene_start_tick", out var sceneStart) ? sceneStart.GetInt32() : start;
        if (start < 0 || end < start || end - start > 32 || rate != 64)
            throw new Exception("Study requires a bounded 64Hz range of at most33frames");
        if (outputStart < 0 || outputStart > start) throw new Exception("Invalid study scene start");
        var controls = config.GetProperty("control_points").EnumerateArray().ToArray();
        if (controls.Length != end - start + 1) throw new Exception("Study input history is incomplete");
        var log = new CaptureLog();
        using var loader = new GameFileLoader(null, config.GetProperty("gameinfo").GetString(), log);
        using var source = loader.LoadFileCompiled(Source) ?? throw new Exception("Missing study source definition");
        var original = (ParticleSystem)source.DataBlock!;
        var op = original.GetOperators().Single(row => row.GetStringProperty("_class") == "C_OP_SetPerChildControlPoint");
        var definition = KVObject.Collection();
        definition["_class"] = new KVObject("CParticleSystemDefinition");
        definition["m_nBehaviorVersion"] = new KVObject(12);
        definition["m_nMaxParticles"] = new KVObject(4);
        definition["m_Operators"] = KVObject.Array([op]);
        definition["m_Children"] = original.GetUpgradedData()["m_Children"];
        var simulation = new ParticleSystemSimulation(ParticleSystem.Create(definition), loader, log) { Name = RootName };
        if (simulation.Children.Count != 4) throw new Exception("Original sprint child count changed");
        var seed = config.GetProperty("seed").GetUInt32();
        var seeds = SeedBinding.Apply(simulation, seed, "controlled-per-child");
        for (var index = 0; index < 4; index++)
        {
            simulation.Particles.Add();
            simulation.Particles.Current[index].UniqueParticleId = index;
            simulation.Particles.Current[index].Index = index;
        }
        var frames = new List<object>();
        for (var index = 0; index < controls.Length; index++)
        {
            var input = controls[index]; var tick = start + index;
            if (input.GetProperty("tick").GetInt32() != tick) throw new Exception("Invalid study tick order");
            var position = Vector(input.GetProperty("position"));
            var q = input.GetProperty("rotation").EnumerateArray().Select(value => value.GetSingle()).ToArray();
            if (q.Length != 4 || q.Any(value => !float.IsFinite(value)) || Math.Abs(q.Sum(value => value * value) - 1f) > .00002f)
                throw new Exception("Invalid study root rotation");
            simulation.MainControlPoint.Position = position;
            simulation.MainControlPoint.Rotation = new(q[0], q[1], q[2], q[3]);
            var points = input.GetProperty("points").EnumerateArray().ToArray();
            var previous = input.GetProperty("previous_points").EnumerateArray().ToArray();
            if (points.Length != 4 || previous.Length != 4 || simulation.Particles.Count != 4)
                throw new Exception("Study must supply four explicit current/previous rig points");
            for (var particle = 0; particle < 4; particle++)
            {
                simulation.Particles.Current[particle].Position = Vector(points[particle]);
                simulation.Particles.Current[particle].PositionPrevious = Vector(previous[particle]);
            }
            if (index == 0) { ControlPointAudit.BeginInputs(simulation, [0]); simulation.Start(); }
            simulation.Update(Math.Max(.000001f, index / (float)rate - simulation.RenderState.Age), tick / (float)rate);
            foreach (var binding in seeds) binding.Verify();
            var systems = new List<object>();
            void Capture(ParticleSystemSimulation system, string path)
            {
                var particles = new List<object>();
                foreach (ref var particle in system.Particles.Current)
                    particles.Add(new { id = particle.UniqueParticleId, position = V(particle.Position), previous = V(particle.PositionPrevious),
                        velocity = V(particle.Velocity), radius = particle.Radius, alpha = particle.Alpha, alpha_alternate = particle.AlphaAlternate,
                        color = V(particle.Color), life = particle.Lifetime, age = particle.Age, creation_time = particle.CreationTime,
                        scratch_float0 = particle.ScratchFloat0, hitbox_index = particle.HitboxIndex, hitbox_coordinates = V(particle.HitboxOffsetPosition),
                        trail = particle.TrailLength, normal = V(particle.Normal), rotation = V(particle.Rotation), sequence = particle.SequenceNumber,
                        manual_frame = particle.ManualAnimationFrame });
                systems.Add(new { name = system.Name, system_path = path, step = system.Particles.CurrentFrameTime, particles });
                for (var child = 0; child < system.Children.Count; child++) Capture(system.Children[child], path + "/" + child);
            }
            Capture(simulation, "root"); frames.Add(new { tick, systems });
        }
        var instance = new {
            id = "controlled-per-child", entity_id = config.GetProperty("entity_id").GetString(), resource = RootName, birth = start, frames,
            control_point_indices = new[] { 0 }, materialized_control_point_indices = ControlPointAudit.Indices(simulation),
            control_point_input_policy = ControlPointAudit.InputProvenance(simulation, "ControlPointInputPolicy"),
            declared_control_point_inputs = ControlPointAudit.InputProvenance(simulation, "DeclaredControlPointInputs"),
            authored_control_point_indices = ControlPointAudit.InputProvenance(simulation, "AuthoredControlPointIndices"),
            unprovided_control_point_indices = ControlPointAudit.InputProvenance(simulation, "UnprovidedControlPointIndices"),
            system_seeds = seeds.Select(binding => new { path = binding.Path, resource = binding.Resource, seed = binding.Value }).ToArray()
        };
        File.WriteAllText(output, JsonSerializer.Serialize(new {
            schema_version = 1, backend = "AGR controlled original sprint child study; not recorded emission",
            request_sha256 = Convert.ToHexStringLower(SHA256.HashData(bytes)), seed, seed_policy = "agr_substitute_sha256_v1",
            start_tick = outputStart, end_tick = end, tick_rate = rate, warnings = log.Messages, instances = new[] { instance },
            scope = "Original installed per-child operator and four original installed rope children. Explicit replay-rig points replace unavailable model/hitbox sampling and bone lock. No original sprint/custom-origin provider coverage."
        }, new JsonSerializerOptions { WriteIndented = true }));
        if (log.Messages.Count != 0) Environment.ExitCode = 2;
    }

    static Vector3 Vector(JsonElement element)
    {
        var values = element.EnumerateArray().Select(value => value.GetSingle()).ToArray();
        if (values.Length != 3 || values.Any(value => !float.IsFinite(value))) throw new Exception("Invalid study rig point");
        return new(values[0], values[1], values[2]);
    }

    static float[] V(Vector3 value) => [value.X, value.Y, value.Z];
}
