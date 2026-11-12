#if AGR_SIMULATION_SNAPSHOT
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;

static class SingleChildPositionOracle
{
    public static void Check(string output)
    {
        var assembly = typeof(ParticleSystemState).Assembly;
        var type = assembly.GetType("ValveResourceFormat.Particles.PreEmissionOperators.SetSingleControlPointPosition")!;
        var parserType = assembly.GetType("ValveResourceFormat.Particles.ParticleDefinitionParser")!;
        object Create(bool once)
        {
            var data = KVObject.Collection();
            data["m_nCP1"] = new KVObject(3);
            data["m_bSetOnce"] = new KVObject(once);
            var position = KVObject.Collection();
            position["m_nType"] = new KVObject("PVEC_TYPE_LITERAL");
            position["m_vLiteralValue"] = KVObject.Array([new KVObject(1f), new KVObject(2f), new KVObject(3f)]);
            data["m_vecCP1Pos"] = position;
            var transform = KVObject.Collection();
            transform["m_nControlPoint"] = new KVObject(2);
            data["m_transformInput"] = transform;
            var parser = Activator.CreateInstance(parserType, data, NullLogger.Instance, 12)!;
            return Activator.CreateInstance(type, parser)!;
        }
        void Apply(object op, ParticleSystemState state) => type.GetMethod("Operate")!.Invoke(op, [state, .1f]);
        var root = SetChildControlOracle.Create();
        var child = SetChildControlOracle.Create(root.RenderState);
        var sibling = SetChildControlOracle.Create(root.RenderState);
        var descendant = SetChildControlOracle.Create(child.RenderState);
        SetChildControlOracle.Attach(root, child); SetChildControlOracle.Attach(root, sibling);
        SetChildControlOracle.Attach(child, descendant);
        var source = root.RenderState.GetControlPoint(2);
        source.Position = new(10, 20, 30); source.Rotation = Quaternion.Identity;
        var rootPoint = root.RenderState.GetControlPoint(3); rootPoint.Position = new(99);
        ControlPointAudit.BeginInputs(root, [2]);
        var op = Create(true); Apply(op, child.RenderState);
        if (child.RenderState.GetControlPoint(3).Position != new Vector3(11, 22, 33)
            || descendant.RenderState.GetControlPoint(3).Position != new Vector3(11, 22, 33)
            || rootPoint.Position != new Vector3(99)) throw new Exception("Child position propagation/root isolation failed");
        source.Position = new(100, 200, 300); Apply(op, child.RenderState);
        if (child.RenderState.GetControlPoint(3).Position != new Vector3(11, 22, 33)) throw new Exception("Set-once changed");
        type.GetMethod("Reset")!.Invoke(op, []); Apply(op, child.RenderState);
        if (child.RenderState.GetControlPoint(3).Position != new Vector3(101, 202, 303)) throw new Exception("Reset did not re-evaluate");
        var roots = (int[])ControlPointAudit.InputProvenance(root, "AuthoredRootPositionIndices")!;
        if (roots.Length != 0 || ((int[])ControlPointAudit.InputProvenance(root, "UnprovidedControlPointIndices")!).Length != 0)
            throw new Exception("Child provenance differs");
        if (sibling.RenderState.GetControlPoint(3).Position != new Vector3(99)) throw new Exception("Sibling was clobbered");
        if (!((int[])ControlPointAudit.InputProvenance(root, "UnprovidedControlPointIndices")!).SequenceEqual([3]))
            throw new Exception("A missing sibling input was hidden");
        Apply(Create(false), child.RenderState);
        if (!((int[])ControlPointAudit.InputProvenance(root, "UnprovidedControlPointIndices")!).SequenceEqual([3]))
            throw new Exception("Later writes erased earlier missing-input evidence");
        var writer = typeof(ParticleSystemState).GetMethod("WriteAuthoredChildPosition", BindingFlags.Instance | BindingFlags.NonPublic)!;
        writer.Invoke(root.RenderState, [7, new Vector3(1)]);
        if (!((int[])ControlPointAudit.InputProvenance(root, "AuthoredRootPositionIndices")!).SequenceEqual([7]))
            throw new Exception("Root write not separately identified");
        var guardRoot = SetChildControlOracle.Create();
        var guardChild = SetChildControlOracle.Create(guardRoot.RenderState);
        SetChildControlOracle.Attach(guardRoot, guardChild);
        ControlPointAudit.BeginInputs(guardRoot, [2, 3]);
        bool refused = false;
        try { Apply(Create(false), guardChild.RenderState); }
        catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { refused = true; }
        if (!refused) throw new Exception("External CP collision admitted");
        File.WriteAllText(output, JsonSerializer.Serialize(new { status = "PASS", checks = 10,
            scope = "Actual literal CP-transform setter, child/descendant isolation, once/reset, missing-input and root telemetry. Not native numerical parity or a normal export." }));
    }
}
#endif
