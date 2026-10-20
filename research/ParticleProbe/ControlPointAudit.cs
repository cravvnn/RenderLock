using System.Collections;
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;

static class ControlPointAudit
{
    public static void BeginInputs(ParticleSystemSimulation simulation, IEnumerable<int> indices)
    {
        // Reflection keeps the helper usable with the previous qualified VRF.
        // Legacy output retains the exact supplied/materialized equality rule.
        typeof(ParticleSystemState).GetMethod("BeginControlPointInputAudit")
            ?.Invoke(simulation.RenderState, new object[] { indices.ToArray() });
    }

    public static object? InputProvenance(ParticleSystemSimulation simulation, string name)
    {
        return typeof(ParticleSystemState).GetProperty(name)?.GetValue(simulation.RenderState);
    }

    // The pinned simulator silently creates a zero-valued CP when an operator
    // requests one. Report materialized indices so the caller can withhold an
    // instance whose required inputs were never supplied. This includes writes;
    // internally authored extra CPs need a separately qualified provider.
    public static int[] Indices(ParticleSystemSimulation root)
    {
        var indices = new SortedSet<int>();
        void Visit(ParticleSystemSimulation simulation)
        {
            foreach (var name in new[] { "controlPoints", "controlPointOverrides" })
            {
                var field = typeof(ParticleSystemState).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)
                    ?? throw new Exception("Pinned control-point state contract changed: " + name);
                var value = field.GetValue(simulation.RenderState);
                if (value == null && name == "controlPointOverrides") continue;
                if (value is not IDictionary dictionary) throw new Exception("Invalid control-point state layout");
                foreach (var key in dictionary.Keys)
                {
                    if (key is not int index || index < 0 || index > 63)
                        throw new Exception("Unsupported materialized control-point index");
                    indices.Add(index);
                }
            }
            foreach (var child in simulation.Children) Visit(child);
        }
        Visit(root);
        return indices.ToArray();
    }

    public static void Check(string output)
    {
        var transform = KVObject.Collection();
        transform["m_nType"] = new KVObject("PT_TYPE_CONTROL_POINT");
        transform["m_nControlPoint"] = new KVObject(7);
        var initializer = KVObject.Collection();
        initializer["_class"] = new KVObject("C_INIT_CreateWithinSphereTransform");
        initializer["m_TransformInput"] = transform;
        var emitter = KVObject.Collection();
        emitter["_class"] = new KVObject("C_OP_InstantaneousEmitter");
        emitter["m_nParticlesToEmit"] = new KVObject(1);
        var definition = KVObject.Collection();
        definition["_class"] = new KVObject("CParticleSystemDefinition");
        definition["m_nBehaviorVersion"] = new KVObject(12);
        definition["m_Emitters"] = KVObject.Array([emitter]);
        definition["m_Initializers"] = KVObject.Array([initializer]);
        var simulation = new ParticleSystemSimulation(ParticleSystem.Create(definition), null!, NullLogger.Instance);
        simulation.MainControlPoint.Position = new Vector3(100, 200, 300);
        simulation.Start();
        simulation.Update(1e-6f, 0);
        var indices = Indices(simulation);
        if (!indices.SequenceEqual(new[] { 0, 7 }) || simulation.Particles.Count != 1)
            throw new Exception("Missing control-point oracle did not exercise the expected dependency");
        File.WriteAllText(output, JsonSerializer.Serialize(new { status = "PASS", supplied = new[] { 0 },
            materialized = indices, particle_count = simulation.Particles.Count,
            scope = "Actual simulator creates an unsupplied CP7; caller must withhold this instance" }));
    }
}
