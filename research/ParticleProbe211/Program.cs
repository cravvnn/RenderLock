// RenderLock particle simulation helper on pristine Source 2 Viewer 21.1 (review port of public b30
// research/ParticleProbe/Program.cs). Same request and response shape. Differences are listed in the response under
// `port_notes`; nothing here is qualified for normal export.
using System.Buffers.Binary;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using ValveResourceFormat.IO;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;

if (args.Length == 1 && args[0] == "--capabilities-v1")
{
    Console.WriteLine(JsonSerializer.Serialize(new { schema_version = 1, commands = new[] { "simulate-world-origin-v1", "inventory-model-hitboxes" } }));
    return;
}
// Authored hitbox metadata only (unchanged b30 command); no simulation.
if (args.Length == 4 && args[0] == "--inventory-model-hitboxes") { ModelHitboxInventory.Run(args[1], args[2], args[3]); return; }
if (args.Length != 2) throw new ArgumentException("REQUEST_JSON OUTPUT_JSON");
var requestBytes = File.ReadAllBytes(args[0]);
var requestSha256 = Convert.ToHexStringLower(SHA256.HashData(requestBytes));
using var document = JsonDocument.Parse(requestBytes); var config = document.RootElement;
var inputRows = config.GetProperty("instances");
var hasSilenceMaterialInputs = inputRows.EnumerateArray().Any(i => i.TryGetProperty("silence_birth_provider", out _));
if (hasSilenceMaterialInputs && inputRows.GetArrayLength() != 1)
    throw new InvalidDataException("Silence material snapshots require one complete qualified instance");
var silenceMaterialFields = hasSilenceMaterialInputs;
var log = new CaptureLog();
uint? seed = config.TryGetProperty("seed", out var seedInput) ? seedInput.GetUInt32() : null;
using var loader = new GameFileLoader(null, config.GetProperty("gameinfo").GetString(), log);
int tickRate = config.GetProperty("tick_rate").GetInt32(), start = config.GetProperty("start_tick").GetInt32(), end = config.GetProperty("end_tick").GetInt32();
if (tickRate <= 0 || start < 0 || end < start || end == int.MaxValue) throw new Exception("Invalid simulation output range or tick rate");
float[] V(Vector3 v) => [v.X, v.Y, v.Z];
float[] F(JsonElement e) => e.EnumerateArray().Select(x => x.GetSingle()).ToArray();
var output = new List<object>();
foreach (var instance in config.GetProperty("instances").EnumerateArray())
{
    var id = instance.GetProperty("id").GetString(); var name = instance.GetProperty("resource").GetString()!; int birth = instance.GetProperty("created_tick").GetInt32();
    var controls = (instance.TryGetProperty("control_points", out var ownControls) ? ownControls : config.GetProperty("control_points")).EnumerateArray().ToDictionary(x => x.GetProperty("tick").GetInt32(), x => x);
    var replayEvents = ReplayEventTimeline.Parse(instance, birth, end, start, tickRate);
    if (birth < 0 || birth > end) throw new Exception("Instance birth must precede output end and be nonnegative");
    var snapshotInputs = SnapshotRuntimeInputs.Parse(instance, controls, birth, end);
    var hitboxInputs = HitboxRuntimeInputs.Parse(instance, controls, birth, end);
    if (replayEvents.SilenceBirthProvider.HasValue && hitboxInputs is null)
        throw new InvalidDataException("Silence birth requires its independent complete hitbox history");
    if (hitboxInputs is not null && snapshotInputs is not null)
        throw new InvalidDataException("Hitbox and snapshot histories cannot be combined");
    // Pre-roll must use an explicit complete history. Never hold the first shot
    // transform backwards or restart an older effect at the output boundary.
    int? modelCount = null; string? extraLayout = null;
    for (int tick = birth; tick <= end; tick++)
    {
        if (!controls.TryGetValue(tick, out var control)) throw new Exception($"{id}: missing control-point history at tick {tick}");
        int boneCount = control.TryGetProperty("model_bones", out var modelBones) ? modelBones.GetArrayLength() : 0;
        string layout = control.TryGetProperty("extra_control_points", out var extraPoints) ? string.Join(",", extraPoints.EnumerateArray().Select(x => x.GetProperty("index").GetInt32()).Order()) : "";
        if (modelCount.HasValue && (modelCount != boneCount || extraLayout != layout)) throw new Exception("Incomplete/changing model or extra control-point history");
        modelCount = boneCount; extraLayout = layout;
        var position = F(control.GetProperty("position")); var rotation = F(control.GetProperty("rotation"));
        if (position.Length != 3 || rotation.Length != 4 || position.Any(v => !float.IsFinite(v)) || rotation.Any(v => !float.IsFinite(v))
            || !float.IsFinite(rotation.Sum(v => v * v)) || rotation.Sum(v => v * v) < 1e-12f)
            throw new Exception($"{id}: invalid control-point transform at tick {tick}");
    }
    if (modelCount > 0) throw new NotSupportedException("CP0 model_bones (bone-only CreateOnModel) is not ported to 21.1 yet: it needs the model identity in the request");
    replayEvents.ValidateControls(controls, birth, end);
    log.Prefix = id!;
    using var resource = loader.LoadFileCompiled(name) ?? throw new Exception("Missing " + name);
    var sim = new ParticleSystemSimulation((ParticleSystem)resource.DataBlock!, loader, log); var frames = new List<object>();
    var supplied = new[] { 0 }.Concat((extraLayout ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(int.Parse)).ToArray();

    // Replay model inputs through 21.1's own model interface.
    var models = new Dictionary<int, ReplayModelObject>(); var modelResources = new List<ValveResourceFormat.Resource>();
    ReplayModelObject ModelAt(int cp, string modelPath)
    {
        if (models.TryGetValue(cp, out var existing))
        {
            if (existing.ModelPath != modelPath) throw new InvalidDataException("One control point is bound to two models");
            return existing;
        }
        var modelResource = loader.LoadFileCompiled(modelPath[..^2]) ?? throw new InvalidDataException("Missing bound model " + modelPath);
        modelResources.Add(modelResource);
        var adapter = new ReplayModelObject((Model)modelResource.DataBlock!, modelPath);
        models[cp] = adapter; sim.GetControlPoint(cp).ModelObject = adapter;
        return adapter;
    }
    if (snapshotInputs is not null)
        foreach (var bindingRow in snapshotInputs.Bindings.EnumerateArray())
            ModelAt(bindingRow.GetProperty("control_point").GetInt32(), bindingRow.GetProperty("model").GetString()!).RequireBone(bindingRow.GetProperty("bone").GetString()!);
    if (hitboxInputs is not null)
        foreach (var bindingRow in hitboxInputs.Bindings.EnumerateArray())
            ModelAt(bindingRow.GetProperty("control_point").GetInt32(), bindingRow.GetProperty("model").GetString()!);

    // One substitute seed per instance; 21.1 derives every child seed from it natively (129 per child slot).
    int? rootSeed = null;
    if (seed.HasValue)
    {
        var key = seed.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) + "\n" + id + "\nroot\n" + sim.Name;
        rootSeed = (int)(BinaryPrimitives.ReadUInt32LittleEndian(SHA256.HashData(Encoding.UTF8.GetBytes(key))) & 0xFFF);
        sim.SeedRandom(rootSeed.Value);
    }
    var systemSeeds = new List<object>();
    void Seeds(ParticleSystemSimulation s, string path) { systemSeeds.Add(new { path, resource = s.Name, seed = s.RenderState.Random.Seed }); for (var i = 0; i < s.Children.Count; i++) Seeds(s.Children[i], path + "/" + i); }
    Seeds(sim, "root");
    var authored = new SortedSet<int>(); var authoredRoot = new SortedSet<int>();
    bool destroyedImmediately = false; float age = 0, lastStep = 0;
    // 21.1 stores velocity as position - previous. A system whose operators rewrite the previous position (Verlet
    // movement) holds one current step of motion. Otherwise previous is whatever the initializer left: velocity times
    // the step in force at that particle's birth (0.05 on a system's first frame), and it never changes.
    var birthSteps = new Dictionary<string, Dictionary<(int, float), float>>();
    var previousMask = ParticleField.PositionPrevious.Mask();
    bool Verlet(ParticleSystemSimulation s) => s.Operators.Any(op => (op.StoredFields & previousMask) != 0);
    // The step initializers saw during this update: 21.1 overwrites PreviousStep with the frame time at the end of
    // each update, and a system's first frame uses 0.05 unless its control points already carry a previous state.
    var initSteps = new Dictionary<string, float>();
    var pendingField = typeof(ParticleSystemSimulation).GetField("firstFramePending", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
        ?? throw new InvalidOperationException("21.1 first-frame field layout changed");
    var validField = typeof(ParticleSystemSimulation).GetField("previousControlPointsValid", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
        ?? throw new InvalidOperationException("21.1 previous-control-point field layout changed");
    void RecordInitSteps(ParticleSystemSimulation s, string path)
    {
        initSteps[path] = (bool)pendingField.GetValue(s)! && !(bool)validField.GetValue(s)! ? 0.05f : s.RenderState.PreviousStep;
        for (var i = 0; i < s.Children.Count; i++) RecordInitSteps(s.Children[i], path + "/" + i);
    }
    void TrackBirthSteps(ParticleSystemSimulation s, string path)
    {
        if (!Verlet(s))
        {
            if (!birthSteps.TryGetValue(path, out var known)) birthSteps[path] = known = [];
            var initStep = initSteps[path];
            var x = s.Particles.Current; var live = new Dictionary<(int, float), float>();
            for (var i = 0; i < x.Length; i++)
            {
                var key = (x.ReadInteger(ParticleField.ParticleId, i), x.ReadFloat(ParticleField.CreationTime, i));
                live[key] = known.TryGetValue(key, out var step) ? step : initStep;
            }
            birthSteps[path] = live;
        }
        for (var i = 0; i < s.Children.Count; i++) TrackBirthSteps(s.Children[i], path + "/" + i);
    }
    for (int tick = birth; tick <= end; tick++)
    {
        snapshotInputs?.Advance(tick);
        hitboxInputs?.Advance(tick);
        if (!destroyedImmediately)
        {
            var cp = controls[tick]; var p = F(cp.GetProperty("position")); var q = F(cp.GetProperty("rotation"));
            sim.MainControlPoint.Position = new Vector3(p[0], p[1], p[2]); sim.MainControlPoint.Rotation = Quaternion.Normalize(new Quaternion(q[0], q[1], q[2], q[3]));
            if (cp.TryGetProperty("extra_control_points", out var extras))
            {
                var seen = new HashSet<int>();
                foreach (var extra in extras.EnumerateArray())
                {
                    var index = extra.GetProperty("index").GetInt32();
                    var ep = F(extra.GetProperty("position")); var eq = F(extra.GetProperty("rotation"));
                    if (index < 1 || index > 63 || !seen.Add(index) || ep.Length != 3 || eq.Length != 4 || ep.Any(x => !float.IsFinite(x))
                        || eq.Any(x => !float.IsFinite(x)) || MathF.Abs(eq.Sum(x => x * x) - 1) > 2e-5f) throw new Exception("Invalid extra control point");
                    var target = sim.GetControlPoint(index); target.Position = new(ep[0], ep[1], ep[2]); target.Rotation = new(eq[0], eq[1], eq[2], eq[3]);
                }
            }
            foreach (var model in models.Values) model.BeginFrame();
            if (snapshotInputs is not null)
                foreach (var (point, bone, binding) in snapshotInputs.ActiveRows()) models[point].SetSnapshotBone(bone, binding.Matrix.Rows);
            if (hitboxInputs is not null)
                foreach (var (point, set, shapes) in hitboxInputs.ActiveSets()) models[point].SetHitboxes(set, shapes);
            if (tick == birth) sim.Start();
            destroyedImmediately = replayEvents.DestroyAt(tick, sim);
            // A tiny first step allows instantaneous emission at the recorded birth tick.
            float targetAge = (tick - birth) / (float)tickRate; float dt = MathF.Max(1e-6f, targetAge - age);
            if (!destroyedImmediately) { RecordInitSteps(sim, "root"); sim.Update(dt, tick / (float)tickRate); age += dt; lastStep = dt; }
            ControlPointAudit.Accumulate(sim, authored, authoredRoot);
            if (!destroyedImmediately) TrackBirthSteps(sim, "root");
        }
        if (tick < start) continue;
        var systems = new List<object>();
        void Capture(ParticleSystemSimulation s, string path)
        {
            var particles = new List<object>();
            var x = s.Particles.Current; var time = s.RenderState.Age;
            var verlet = Verlet(s); birthSteps.TryGetValue(path, out var births);
            for (var i = 0; i < x.Length && !destroyedImmediately; i++)
            {
                var pos = x.ReadVector(ParticleField.Position, i); var prev = x.ReadVector(ParticleField.PositionPrevious, i);
                var created = x.ReadFloat(ParticleField.CreationTime, i);
                var velocityStep = verlet ? (s.RenderState.CurrentStep > 0 ? s.RenderState.CurrentStep : s.RenderState.PreviousStep) : births![(x.ReadInteger(ParticleField.ParticleId, i), created)];
                if (!(velocityStep > 0)) throw new InvalidOperationException("No velocity step for a particle");
                // Consumers read trail length as |position - previous| / step, so previous is restated for the step
                // this frame reports. It equals 21.1's own value wherever an operator maintains previous at this step.
                var velocity = (pos - prev) / velocityStep; prev = pos - velocity * lastStep;
                var sample = new Dictionary<string, object>
                {
                    ["id"] = x.ReadInteger(ParticleField.ParticleId, i), ["position"] = V(pos), ["previous"] = V(prev),
                    ["velocity"] = V(velocity), ["radius"] = x.ReadFloat(ParticleField.Radius, i),
                    ["alpha"] = x.ReadFloat(ParticleField.Alpha, i), ["alpha_alternate"] = x.ReadFloat(ParticleField.AlphaAlternate, i),
                    ["color"] = V(x.ReadVector(ParticleField.Color, i)), ["life"] = x.ReadFloat(ParticleField.LifeDuration, i),
                    ["age"] = time - created, ["creation_time"] = created, ["scratch_float0"] = x.ReadFloat(ParticleField.ScratchFloat, i),
                    ["shader_extra_data1"] = x.ReadFloat(ParticleField.ShaderExtraData1, i), ["shader_extra_data2"] = x.ReadFloat(ParticleField.ShaderExtraData2, i),
                    ["hitbox_index"] = x.ReadInteger(ParticleField.HitboxIndex, i), ["hitbox_coordinates"] = V(x.ReadVector(ParticleField.HitboxOffsetPosition, i)),
                    ["trail"] = x.ReadFloat(ParticleField.TrailLength, i), ["normal"] = V(x.ReadVector(ParticleField.Normal, i)),
                    ["rotation"] = new[] { x.ReadFloat(ParticleField.Yaw, i), x.ReadFloat(ParticleField.Pitch, i), x.ReadFloat(ParticleField.Roll, i) },
                    ["sequence"] = x.ReadFloat(ParticleField.SequenceNumber, i), ["second_sequence"] = x.ReadFloat(ParticleField.SecondSequenceNumber, i),
                    ["manual_frame"] = x.ReadFloat(ParticleField.ManualAnimationFrame, i),
                };
                if (silenceMaterialFields)
                {
                    sample["scratch_float1"] = x.ReadFloat(ParticleField.ScratchFloat1, i); sample["scratch_float2"] = x.ReadFloat(ParticleField.ScratchFloat2, i);
                    sample["material_scratch_vector"] = V(x.ReadVector(ParticleField.ScratchVector, i));
                }
                particles.Add(sample);
            }
            systems.Add(new { name = s.Name, system_path = path, step = lastStep, system_age = s.RenderState.Age, control_points = RenderControlSnapshot.Capture(s.RenderState), particles });
            for (int childIndex = 0; childIndex < s.Children.Count; childIndex++) Capture(s.Children[childIndex], path + "/" + childIndex);
        }
        Capture(sim, "root"); frames.Add(new { tick, systems });
    }
    var (materialized, snapshotCarriers) = ControlPointAudit.Indices(sim);
    var row = new Dictionary<string, object?>
    {
        ["id"] = id, ["resource"] = name, ["birth"] = birth, ["frames"] = frames, ["model_bone_count"] = modelCount ?? 0,
        ["control_point_indices"] = supplied, ["materialized_control_point_indices"] = materialized,
        ["control_point_input_policy"] = ControlPointAudit.Policy, ["snapshot_carrier_control_point_indices"] = snapshotCarriers, ["declared_control_point_inputs"] = supplied,
        ["authored_control_point_indices"] = authored.ToArray(),
        ["unprovided_control_point_indices"] = materialized.Except(supplied).Except(authored).ToArray(),
        ["system_seeds"] = systemSeeds, ["system_seed_resets"] = Array.Empty<object>(),
        ["replay_event_policy"] = ReplayEventTimeline.Policy, ["literal_transforms_applied"] = replayEvents.LiteralReport,
        ["destroy_applied"] = replayEvents.DestroyReport, ["release_ticks"] = replayEvents.ReleaseTicks,
        ["model_objects"] = models.OrderBy(m => m.Key).Select(m => new { control_point = m.Key, report = m.Value.Report }).ToArray(),
    };
    if (snapshotInputs is not null)
    {
        row["snapshot_model_input_policy"] = SnapshotRuntimeInputs.Policy; row["snapshot_model_bindings"] = snapshotInputs.Bindings;
        row["snapshot_model_queried_binding_ids"] = snapshotInputs.Complete(); row["authored_root_position_indices"] = authoredRoot.ToArray();
        if (replayEvents.CrossbowOwnerProvider.HasValue) row["crossbow_owner_provider"] = replayEvents.CrossbowOwnerProvider.Value;
    }
    if (hitboxInputs is not null) { row["model_hitbox_input_policy"] = HitboxRuntimeInputs.Policy; row["model_hitbox_bindings"] = hitboxInputs.Bindings; }
    if (replayEvents.SilenceBirthProvider.HasValue) { row["silence_birth_provider"] = replayEvents.SilenceBirthProvider.Value; row["authored_root_position_indices"] = authoredRoot.ToArray(); }
    if (replayEvents.WorldOriginProvider is not null) row["world_origin_provider"] = replayEvents.WorldOriginProvider.Value;
    if (instance.TryGetProperty("owned_broad_provider", out var ownedBroad))
    {
        if (ownedBroad.ValueKind != JsonValueKind.Object || ownedBroad.EnumerateObject().Count() != 1
            || ownedBroad.GetProperty("policy").GetString() != "agr_owned_broad_unverified_v1"
            || replayEvents.WorldOriginProvider is not null)
            throw new InvalidDataException("Unknown or combined broad owned provider");
        row["owned_broad_provider"] = ownedBroad.Clone();
    }
    if (models.Count > 0 && models.Values.Any(m => ((dynamic)m.Report).pose_reads == 0)) throw new InvalidDataException("A bound replay model was never read by the simulation");
    output.Add(row);
    foreach (var modelResource in modelResources) modelResource.Dispose();
}
var response = new Dictionary<string, object?>
{
    ["schema_version"] = 1,
    ["backend"] = "Source 2 Viewer 21.1 942df5b37ebbde6153f6c488880b91d4bed003e6 unmodified + RenderLock replay model adapter v1 (review port, not qualified)",
    ["control_point_snapshot_policy"] = RenderControlSnapshot.Policy, ["shader_extra_data_policy"] = "agr_shader_extra_fields_v1",
    ["request_sha256"] = requestSha256, ["seed"] = seed, ["seed_policy"] = seed.HasValue ? "agr_substitute_sha256_root_s2v211_v1" : "upstream_random",
    ["tick_rate"] = tickRate, ["start_tick"] = start, ["end_tick"] = end, ["warnings"] = log.Messages, ["instances"] = output,
    ["port_notes"] = new[]
    {
        "simulation kernels are Source 2 Viewer 21.1's own; no RenderLock simulation patches",
        "one substitute seed per instance root; child seeds derived by 21.1; no endcap seed resets",
        "velocity is (position - previous) / step: the current step where an operator rewrites previous, else the step in force at the particle's birth; age is system age - creation time",
        "previous is position - velocity * reported step, not 21.1's stored previous position",
        "control-point audit: materialized / function-written sets; reads cannot be told from writes on 21.1",
        "snapshot_model_queried_binding_ids lists bindings fed to 21.1, not bindings a kernel queried",
        "unwritten shader extra data defaults to 1 on 21.1 (0 on the 42743fb helper)",
    },
};
if (silenceMaterialFields) { response["model_material_fields_policy"] = "agr_model_material_fields_v1"; response["model_aux_scalar_fields_policy"] = "agr_model_aux_scalar_fields_v1"; }
File.WriteAllText(args[1], JsonSerializer.Serialize(response, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"Simulated {output.Count} recorded instances; {log.Messages.Count} warnings");
if (log.Messages.Count > 0) Environment.ExitCode = 2;

class CaptureLog : ILogger
{
    public List<string> Messages = new(); public string Prefix = "";
    public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
    public bool IsEnabled(LogLevel level) => level >= LogLevel.Warning;
    public void Log<TState>(LogLevel level, EventId id, TState state, Exception? error, Func<TState, Exception?, string> format) { if (IsEnabled(level)) Messages.Add(Prefix + ": " + format(state, error)); }
}
