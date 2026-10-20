using System.Collections;
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;

static class SetChildControlOracle
{
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    static readonly Assembly Vrf = typeof(ParticleSystemSimulation).Assembly;
    static Type Operator => Vrf.GetType("ValveResourceFormat.Particles.Operators.SetChildControlPoints")
        ?? throw new Exception("The qualified child-control patch is absent");
    static readonly Type Parser = Vrf.GetType("ValveResourceFormat.Particles.ParticleDefinitionParser")!;

    static KVObject Definition(KVObject? op = null, bool emitAtChildPoint = false)
    {
        var definition = KVObject.Collection();
        definition["_class"] = new KVObject("CParticleSystemDefinition");
        definition["m_nBehaviorVersion"] = new KVObject(12);
        if (op != null) definition["m_Operators"] = KVObject.Array([op]);
        if (emitAtChildPoint)
        {
            var emitter = KVObject.Collection();
            emitter["_class"] = new KVObject("C_OP_InstantaneousEmitter");
            emitter["m_nParticlesToEmit"] = new KVObject(1);
            var transform = KVObject.Collection();
            transform["m_nType"] = new KVObject("PT_TYPE_CONTROL_POINT");
            transform["m_nControlPoint"] = new KVObject(3);
            var initializer = KVObject.Collection();
            initializer["_class"] = new KVObject("C_INIT_CreateWithinSphereTransform");
            initializer["m_TransformInput"] = transform;
            initializer["m_fRadiusMin"] = new KVObject(0f);
            initializer["m_fRadiusMax"] = new KVObject(0f);
            definition["m_Emitters"] = KVObject.Array([emitter]);
            definition["m_Initializers"] = KVObject.Array([initializer]);
        }
        return definition;
    }

    internal static ParticleSystemSimulation Create(ParticleSystemState? parent = null, int group = 0,
        KVObject? op = null, bool emitAtChildPoint = false, ILogger? log = null)
    {
        var sim = new ParticleSystemSimulation(ParticleSystem.Create(Definition(op, emitAtChildPoint)),
            null!, log ?? NullLogger.Instance, parentSystemState: parent);
        typeof(ParticleSystemSimulation).GetField("groupId", Private)!.SetValue(sim, group);
        return sim;
    }

    internal static void Attach(ParticleSystemSimulation parent, ParticleSystemSimulation child)
    {
        ((List<ParticleSystemSimulation>)typeof(ParticleSystemSimulation).GetField("childSimulations", Private)!
            .GetValue(parent)!).Add(child);
    }

    static KVObject Op(int first = 3, int count = 1, bool reverse = false)
    {
        var op = KVObject.Collection();
        op["_class"] = new KVObject("C_OP_SetChildControlPoints");
        op["m_nFirstControlPoint"] = new KVObject(first);
        op["m_nNumControlPoints"] = new KVObject(count);
        op["m_bReverse"] = new KVObject(reverse);
        return op;
    }

    static object ParseOperator(KVObject op, ILogger? logger = null)
    {
        var parser = Activator.CreateInstance(Parser, op, logger ?? NullLogger.Instance, 12)!;
        return Activator.CreateInstance(Operator, parser)!;
    }

    internal static ControlPoint OwnPoint(ParticleSystemSimulation sim, int index)
        => (ControlPoint)typeof(ParticleSystemState).GetMethod("OverrideControlPoint", Private)!
            .Invoke(sim.RenderState, [index])!;

    static void Apply(object op, ParticleSystemSimulation sim)
        => Operator.GetMethod("Operate")!.Invoke(op, [sim.Particles, .75f, sim.RenderState, 1f]);

    static int[] Indices(ParticleSystemSimulation root, string name)
        => (int[])(ControlPointAudit.InputProvenance(root, name)
            ?? throw new Exception("Control-point provenance is missing: " + name));

    public static void Check(string nativeJson, string output)
    {
        using var native = JsonDocument.Parse(File.ReadAllBytes(nativeJson));
        var defaults = native.RootElement.GetProperty("default_control_point_frame");
        if (defaults.GetProperty("count").GetInt32() != 64
            || !defaults.GetProperty("forward").EnumerateArray().Select(x => x.GetInt32()).SequenceEqual([1, 0, 0])
            || !defaults.GetProperty("up").EnumerateArray().Select(x => x.GetInt32()).SequenceEqual([0, 0, 1])
            || !defaults.GetProperty("side").EnumerateArray().Select(x => x.GetInt32()).SequenceEqual([0, 1, 0])
            || new ControlPoint().GetRotation() != Quaternion.Identity)
            throw new Exception("The newly authored control point's default frame is not native identity");
        var cases = 0;
        foreach (var row in native.RootElement.GetProperty("cases").EnumerateArray())
        {
            var root = Create();
            var child = Create(root.RenderState);
            var sibling = Create(root.RenderState, group: 7);
            var grandchild = Create(child.RenderState, group: 7);
            Attach(root, child); Attach(root, sibling); Attach(child, grandchild);
            var descendants = new[] { child, sibling, grandchild };
            var rotation = Quaternion.CreateFromAxisAngle(Vector3.UnitZ, .75f);
            foreach (var descendant in descendants)
                for (var index = 0; index < 64; index++)
                {
                    var point = OwnPoint(descendant, index);
                    point.Position = new(-999f);
                    point.Rotation = rotation;
                    point.Orientation = Vector3.UnitY;
                }
            foreach (var position in row.GetProperty("positions").EnumerateArray())
            {
                var p = position.EnumerateArray().Select(x => x.GetSingle()).ToArray();
                root.Particles.Add();
                root.Particles.Current[^1].Position = new(p[0], p[1], p[2]);
            }
            var first = row.GetProperty("first_control_point").GetInt32();
            var op = ParseOperator(Op(first, row.GetProperty("requested_count").GetInt32(), row.GetProperty("reverse").GetBoolean()));
            Apply(op, root);
            var expectedChildren = row.GetProperty("children").EnumerateArray().ToArray();
            for (var childIndex = 0; childIndex < descendants.Length; childIndex++)
            {
                var expected = expectedChildren[childIndex].EnumerateArray().ToDictionary(
                    x => x.GetProperty("cp").GetInt32(), x => x.GetProperty("position").EnumerateArray().Select(v => v.GetSingle()).ToArray());
                for (var index = 0; index < 64; index++)
                {
                    var point = OwnPoint(descendants[childIndex], index);
                    var position = expected.TryGetValue(index, out var p) ? new Vector3(p[0], p[1], p[2]) : new Vector3(-999f);
                    if (point.Position != position || point.Rotation != rotation || point.Orientation != Vector3.UnitY)
                        throw new Exception($"Native child-position mismatch in case {cases}, child {childIndex}, CP{index}");
                }
            }
            if (ControlPointAudit.Indices(root).Length != 64) throw new Exception("Oracle CP inventory changed");
            cases++;
        }

        var functionalLog = new CaptureLog();
        var functional = Create(op: Op(), log: functionalLog);
        var receiver = Create(functional.RenderState, emitAtChildPoint: true, log: functionalLog);
        Attach(functional, receiver);
        functional.MainControlPoint.Position = new(100, 200, 300);
        functional.Particles.Add();
        functional.Particles.Current[0].Position = new(1234, -5678, 9012);
        ControlPointAudit.BeginInputs(functional, [0]);
        functional.Start();
        functional.Update(1e-6f, 0f);
        if (functionalLog.Messages.Count != 0 || receiver.Particles.Count != 1
            || receiver.Particles.Current[0].Position != functional.Particles.Current[0].Position
            || !Indices(functional, "AuthoredControlPointIndices").SequenceEqual([3])
            || Indices(functional, "UnprovidedControlPointIndices").Length != 0
            || receiver.RenderState.GetControlPoint(3).GetRotation() != Quaternion.Identity
            || !ControlPointAudit.Indices(functional).SequenceEqual([0, 3]))
            throw new Exception("The actual tree did not consume a preceding authored CP3 position");

        var late = Create(); var lateChild = Create(late.RenderState); Attach(late, lateChild);
        late.MainControlPoint.Position = Vector3.One;
        ControlPointAudit.BeginInputs(late, [0]);
        _ = lateChild.RenderState.GetControlPoint(3).Position;
        late.Particles.Add(); late.Particles.Current[0].Position = new(4, 5, 6);
        Apply(ParseOperator(Op()), late);
        if (!Indices(late, "UnprovidedControlPointIndices").SequenceEqual([3])
            || !Indices(late, "AuthoredControlPointIndices").SequenceEqual([3]))
            throw new Exception("An authored write erased the preceding unsupplied CP read");
        _ = lateChild.RenderState.GetControlPoint(7).Position;
        if (!Indices(late, "UnprovidedControlPointIndices").SequenceEqual([3, 7]))
            throw new Exception("An unrelated unsupplied dependency was hidden");

        var collision = Create(); var collisionChild = Create(collision.RenderState); Attach(collision, collisionChild);
        ControlPointAudit.BeginInputs(collision, [0]);
        collision.Particles.Add();
        try { Apply(ParseOperator(Op(0)), collision); throw new Exception("External CP replacement was accepted"); }
        catch (TargetInvocationException e) when (e.InnerException is InvalidOperationException) { }
        var rejected = 0;
        foreach (var invalid in new[] { "orientation", "source", "first", "count" })
        {
            var log = new CaptureLog(); var op = Op();
            if (invalid == "orientation") op["m_bSetOrientation"] = new KVObject(true);
            if (invalid == "source") op["m_nFirstSourcePoint"] = new KVObject(1f);
            if (invalid == "first") op["m_nFirstControlPoint"] = new KVObject(64);
            if (invalid == "count") op["m_nNumControlPoints"] = new KVObject(-1);
            _ = ParseOperator(op, log);
            if (log.Messages.Count != 1) throw new Exception("Unqualified child operator variant did not warn: " + invalid);
            rejected++;
        }
        File.WriteAllText(output, JsonSerializer.Serialize(new {
            status = "PASS", native_reference_cases = cases, native_sha256 = native.RootElement.GetProperty("particles_sha256").GetString(),
            recursive_descendant_writes = true, unmatched_sibling_preserved = true, existing_orientation_preserved = true,
            new_control_point_frame_native_identity = true,
            actual_tree_cp3_consumed = true, read_before_write_retained = true, unrelated_unsupplied_cp_rejected = true,
            external_transform_collision_rejected = true, unqualified_variants_warned = rejected,
            scope = "Position-only, literal first source 0. Synthetic actual simulator tree plus 90 native position-store cases; no named-provider, orientation-authoring or original tester effect coverage."
        }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
