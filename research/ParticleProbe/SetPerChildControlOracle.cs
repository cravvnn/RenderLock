using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;

static class SetPerChildControlOracle
{
    static readonly Assembly Vrf = typeof(ParticleSystemSimulation).Assembly;
    static Type Operator => Vrf.GetType("ValveResourceFormat.Particles.Operators.SetPerChildControlPoint")
        ?? throw new Exception("The qualified per-child patch is absent");

    static KVObject Op(int increment = 1, bool orient = true, int controlPoint = 3, int count = 4)
    {
        var op = KVObject.Collection();
        op["_class"] = new KVObject("C_OP_SetPerChildControlPoint");
        op["m_nFirstControlPoint"] = new KVObject(controlPoint);
        op["m_nNumControlPoints"] = new KVObject(count);
        op["m_nParticleIncrement"] = new KVObject((float)increment);
        op["m_bSetOrientation"] = new KVObject(orient);
        return op;
    }

    static object Parse(KVObject op, CaptureLog? log = null)
    {
        var parserType = Vrf.GetType("ValveResourceFormat.Particles.ParticleDefinitionParser")!;
        var parser = Activator.CreateInstance(parserType, op,
            (Microsoft.Extensions.Logging.ILogger?)log ?? NullLogger.Instance, 12)!;
        return Activator.CreateInstance(Operator, parser)!;
    }

    static void Apply(object op, ParticleSystemSimulation root)
        => Operator.GetMethod("Operate")!.Invoke(op, [root.Particles, .75f, root.RenderState, 1f]);

    static Vector3 Vector(JsonElement value)
    {
        var parts = value.EnumerateArray().Select(x => x.GetSingle()).ToArray();
        return new(parts[0], parts[1], parts[2]);
    }

    static Matrix4x4 Frame(ControlPoint point)
        => typeof(ControlPoint).GetProperty("Basis")?.GetValue(point) is Matrix4x4 basis
            ? basis : Matrix4x4.CreateFromQuaternion(point.GetRotation());

    static Vector3[] Rows(Matrix4x4 matrix)
        => [new(matrix.M11, matrix.M12, matrix.M13), new(matrix.M21, matrix.M22, matrix.M23),
            new(matrix.M31, matrix.M32, matrix.M33)];

    public static void Check(string nativeJson, string output)
    {
        using var native = JsonDocument.Parse(File.ReadAllBytes(nativeJson));
        var cases = 0; var maxFrameError = 0f; var nonorthogonalFrames = 0;
        foreach (var row in native.RootElement.GetProperty("cases").EnumerateArray())
        {
            var controlPoint = row.GetProperty("first_control_point").GetInt32();
            var root = SetChildControlOracle.Create();
            var children = row.GetProperty("child_groups").EnumerateArray().Select(group =>
                SetChildControlOracle.Create(root.RenderState, group: group.GetInt32())).ToList();
            foreach (var child in children) SetChildControlOracle.Attach(root, child);
            if (children.Count > 0)
            {
                var grandchild = SetChildControlOracle.Create(children[0].RenderState, group: 7);
                SetChildControlOracle.Attach(children[0], grandchild);
                children.Add(grandchild);
            }
            foreach (var child in children)
                for (var cp = 0; cp < 64; cp++)
                {
                    var point = SetChildControlOracle.OwnPoint(child, cp);
                    point.Position = new(-999f);
                    point.Rotation = Quaternion.Identity;
                    point.Orientation = Vector3.UnitX;
                }
            var previous = row.GetProperty("previous_positions").EnumerateArray().ToArray();
            var particleIndex = 0;
            foreach (var position in row.GetProperty("positions").EnumerateArray())
            {
                root.Particles.Add();
                root.Particles.Current[^1].Position = Vector(position);
                root.Particles.Current[^1].PositionPrevious = Vector(previous[particleIndex++]);
            }
            Apply(Parse(Op(row.GetProperty("increment").GetInt32(), row.GetProperty("orient").GetBoolean(),
                controlPoint, row.GetProperty("requested_count").GetInt32())), root);
            var expectedChildren = row.GetProperty("children").EnumerateArray().ToArray();
            for (var childIndex = 0; childIndex < children.Count; childIndex++)
            {
                var point = SetChildControlOracle.OwnPoint(children[childIndex], controlPoint);
                var expected = expectedChildren[childIndex];
                if (point.Position != Vector(expected.GetProperty("position")))
                    throw new Exception($"Per-child position mismatch in case {cases}, child {childIndex}");
                var actualFrame = Rows(Frame(point));
                var expectedFrame = expected.GetProperty("frame").EnumerateArray().Select(Vector).ToArray();
                for (var axis = 0; axis < 3; axis++)
                {
                    var error = Vector3.Abs(actualFrame[axis] - expectedFrame[axis]);
                    maxFrameError = Math.Max(maxFrameError, Math.Max(error.X, Math.Max(error.Y, error.Z)));
                }
                if (maxFrameError > 0.000002f) throw new Exception("Per-child native orientation frame mismatch: " + maxFrameError);
                var provider = Vrf.GetType("ValveResourceFormat.Particles.ControlPointTransformProvider")!;
                var transformDirection = provider.GetMethod("TransformDirection", BindingFlags.Public | BindingFlags.Static)!;
                var localDirection = new Vector3(.125f, 2f, -3f);
                var transformed = (Vector3)transformDirection.Invoke(null,
                    [children[childIndex].RenderState, controlPoint, localDirection])!;
                if (Vector3.Distance(transformed, Vector3.TransformNormal(localDirection, Frame(point))) > .000002f)
                    throw new Exception("The transform provider discarded the explicit per-child frame");
                if (Math.Abs(Vector3.Dot(actualFrame[0], actualFrame[1])) > .00001f)
                {
                    try { _ = point.GetRotation(); throw new Exception("A nonorthogonal basis was silently converted to a quaternion"); }
                    catch (InvalidOperationException) { nonorthogonalFrames++; }
                }
                for (var cp = 0; cp < 64; cp++)
                    if (cp != controlPoint && SetChildControlOracle.OwnPoint(children[childIndex], cp).Position != new Vector3(-999f))
                        throw new Exception("Per-child operator wrote an unrelated point");
            }
            cases++;
        }

        var log = new CaptureLog();
        var functional = SetChildControlOracle.Create(op: Op(), log: log);
        var targets = Enumerable.Range(0, 4).Select(_ =>
            SetChildControlOracle.Create(functional.RenderState, emitAtChildPoint: true, log: log)).ToArray();
        foreach (var target in targets) SetChildControlOracle.Attach(functional, target);
        functional.MainControlPoint.Position = new(100, 200, 300);
        for (var index = 0; index < 4; index++)
        {
            functional.Particles.Add();
            functional.Particles.Current[index].Position = new(1000 + index, 2000 + index, 3000 + index);
            functional.Particles.Current[index].PositionPrevious = new(999 + index, 1998 + index, 2997 + index);
        }
        ControlPointAudit.BeginInputs(functional, [0]);
        functional.Start(); functional.Update(1e-6f, 0f);
        for (var index = 0; index < targets.Length; index++)
            if (targets[index].Particles.Count != 1 || targets[index].Particles.Current[0].Position != functional.Particles.Current[index].Position)
                throw new Exception("The real per-child tree did not initialize at its unique source particle");
        if (log.Messages.Count != 0 || !ControlPointAudit.Indices(functional).SequenceEqual([0, 3])
            || ControlPointAudit.InputProvenance(functional, "ControlPointInputPolicy") as string != "agr_authored_child_controls_v2"
            || ControlPointAudit.InputProvenance(functional, "UnprovidedControlPointIndices") is not int[] missing || missing.Length != 0)
            throw new Exception("Per-child tree control-point provenance failed");

        var unsupported = 0;
        foreach (var field in new[] { "source", "increment", "countMode", "attribute", "cp" })
        {
            var capture = new CaptureLog(); var op = Op();
            if (field == "source") op["m_nFirstSourcePoint"] = new KVObject(1f);
            if (field == "increment") op["m_nParticleIncrement"] = new KVObject(2f);
            if (field == "countMode") op["m_bNumAsCount"] = new KVObject(true);
            if (field == "attribute") { op["m_bSetOrientation"] = new KVObject(false); op["m_nOrientation"] = new KVObject(21); }
            if (field == "cp") op["m_nFirstControlPoint"] = new KVObject(64);
            _ = Parse(op, capture);
            if (capture.Messages.Count != 1) throw new Exception("Unqualified per-child variant did not warn: " + field);
            unsupported++;
        }
        if (nonorthogonalFrames == 0) throw new Exception("The native near-vertical frame branch was not exercised");
        File.WriteAllText(output, JsonSerializer.Serialize(new {
            status = "PASS", native_cases = cases, max_frame_component_error = maxFrameError,
            exact_positions = true, descendants_and_unmatched_siblings = true,
            raw_basis_transform_consumer = true,
            actual_four_child_initializers = true, nonorthogonal_frame_quaternion_rejections = nonorthogonalFrames,
            unsupported_variants_warned = unsupported,
            scope = "Literal first source0, increment +/-1, fixed count; raw native velocity basis preserved. Synthetic real simulator tree; no original sprint provider/render qualification. Nonorthogonal changing-basis interpolation remains explicitly unavailable."
        }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
