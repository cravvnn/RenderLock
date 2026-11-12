using System.Collections;
using System.Numerics;
using System.Reflection;
using ValveResourceFormat.Particles;

// Read the pinned simulator's effective per-system values without calling
// GetControlPoint: that getter invents a zero value for a missing input.
static class RenderControlSnapshot
{
    public const string Policy = "agr_visible_control_points_v1";

    static readonly FieldInfo Points = Field("controlPoints");
    static readonly FieldInfo Overrides = Field("controlPointOverrides");

    static FieldInfo Field(string name) => typeof(ParticleSystemState)
        .GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)
        ?? throw new InvalidOperationException("Pinned control-point snapshot layout changed: " + name);

    static void Merge(object? source, SortedDictionary<int, ControlPoint> target)
    {
        if (source is not IDictionary dictionary)
            throw new InvalidOperationException("Invalid control-point snapshot dictionary");
        foreach (DictionaryEntry entry in dictionary)
        {
            if (entry.Key is not int index || index is < 0 or > 63 || entry.Value is not ControlPoint point)
                throw new InvalidOperationException("Invalid control-point snapshot entry");
            target[index] = point;
        }
    }

    static SortedDictionary<int, ControlPoint> Visible(ParticleSystemState state)
    {
        // Match GetControlPoint's lookup: local override, then ancestor, then
        // root dictionary. A child's ordinary dictionary is not a local override.
        var result = state.ParentSystem is { } parent ? Visible(parent) : new();
        if (state.ParentSystem == null) Merge(Points.GetValue(state), result);
        if (Overrides.GetValue(state) is { } overrides) Merge(overrides, result);
        return result;
    }

    public static object[] Capture(ParticleSystemState state) => Visible(state)
        .Select(entry =>
        {
            var p = entry.Value.Position;
            if (!float.IsFinite(p.X) || !float.IsFinite(p.Y) || !float.IsFinite(p.Z))
                throw new InvalidOperationException("Non-finite render control point");
            return (object)new { index = entry.Key, position = new[] { p.X, p.Y, p.Z } };
        }).ToArray();

    public static void Check(string output)
    {
        var root = new ParticleSystemState();
        root.SetControlPointValue(0, new(10, 20, 30));
        root.SetControlPointValue(5, new(.25f, 2, 3));
        var child = new ParticleSystemState(root);
        var sibling = new ParticleSystemState(root);
        // Exercise a real local override, not a second external root input.
        var method = typeof(ParticleSystemState).GetMethod("OverrideControlPoint", BindingFlags.Instance | BindingFlags.NonPublic)
            ?? throw new Exception("Pinned child override contract changed");
        ((ControlPoint)method.Invoke(child, new object[] { 5 })!).Position = new(.75f, 4, 5);
        root.BeginControlPointInputAudit(new[] { 0, 5 });
        string Json(ParticleSystemState s) => System.Text.Json.JsonSerializer.Serialize(Capture(s));
        var rootJson = Json(root);
        var childJson = Json(child);
        var siblingJson = Json(sibling);
        if (rootJson != "[{\"index\":0,\"position\":[10,20,30]},{\"index\":5,\"position\":[0.25,2,3]}]"
            || childJson != "[{\"index\":0,\"position\":[10,20,30]},{\"index\":5,\"position\":[0.75,4,5]}]"
            || siblingJson != rootJson || root.UnprovidedControlPointIndices.Length != 0
            || root.HighestControlPoint != 5)
            throw new Exception("Render snapshot inheritance or read-only check failed");
        File.WriteAllText(output, System.Text.Json.JsonSerializer.Serialize(new
        {
            status = "PASS", policy = Policy, root = Capture(root), child = Capture(child),
            sibling = Capture(sibling), missing_inputs = root.UnprovidedControlPointIndices,
            scope = "Pinned simulator snapshot inheritance, child isolation and non-materialization; not engine visual parity"
        }));
    }
}
