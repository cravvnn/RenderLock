// Source 2 Viewer 21.1 port support: value types the request parsers used from the old patched dependency, the model
// adapter that feeds replay poses through 21.1's own IParticleModelObject, and the control-point audit on 21.1.
using System.Collections.Immutable;
using System.Numerics;
using System.Reflection;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.ResourceTypes.ModelData;

namespace ValveResourceFormat.Particles.Utils
{
    /// <summary>3x4 Source column-vector matrix (current bone-to-world times inverse rest), as validated by the host.</summary>
    public readonly struct SnapshotSkinningMatrix
    {
        public float[] Rows { get; }
        public SnapshotSkinningMatrix(ReadOnlySpan<float> rows)
        {
            if (rows.Length != 12) throw new InvalidDataException("Snapshot matrix requires exactly twelve scalar values");
            Rows = rows.ToArray();
        }
    }
}

namespace ValveResourceFormat.Particles
{
    public readonly record struct SnapshotModelBinding(string Identity, Utils.SnapshotSkinningMatrix Matrix);
}

/// <summary>
/// One control point's replay model inputs as a 21.1 model object. Named-bone snapshot frames replace the bind pose
/// of that bone by bind * frame (21.1 then skins with inverseBind * pose). Hitbox frames set the pose of each
/// hitbox's bone to the recorded world frame. Fail closed where 21.1 itself would fall back silently.
/// </summary>
sealed class ReplayModelObject(Model model, string modelPath) : IParticleModelObject
{
    private readonly Dictionary<string, int> boneIndices = new(StringComparer.Ordinal);
    private readonly Dictionary<int, Matrix4x4> snapshotFrames = [];
    private readonly Dictionary<int, Matrix4x4> hitboxPoses = [];
    private Matrix4x4[]? bind;
    private Matrix4x4[]? worldPose;
    private float maxBoundsError;
    private int checkedBoxes, checkedFrames, poseReads;

    public Model Model { get; } = model;
    public string ModelPath { get; } = modelPath;
    public Matrix4x4 Transform => Matrix4x4.Identity;
    public object Report => new { model = ModelPath, bones = Model.Skeleton.Bones.Length, pose_reads = poseReads,
        hitbox_frames_checked = checkedFrames, hitbox_boxes_checked = checkedBoxes, hitbox_max_bounds_error = maxBoundsError };

    public void RequireBone(string bone)
    {
        var index = Model.Skeleton.GetBoneIndex(bone);
        if (index < 0) throw new InvalidDataException("Snapshot bone is not in the model skeleton: " + bone);
        boneIndices[bone] = index;
    }

    public void SetSnapshotBone(string bone, float[] m)
    {
        if (m.Length != 12 || !boneIndices.TryGetValue(bone, out var index)) throw new InvalidDataException("Unbound snapshot bone frame");
        snapshotFrames[index] = new Matrix4x4(m[0], m[4], m[8], 0, m[1], m[5], m[9], 0, m[2], m[6], m[10], 0, m[3], m[7], m[11], 1);
    }

    public void SetHitboxes(string setName, ImmutableArray<AuthoredHitboxShape> shapes)
    {
        var modelSet = Model.HitboxSets.FirstOrDefault(x => string.Equals(x.Key, setName, StringComparison.OrdinalIgnoreCase)).Value
            ?? throw new InvalidDataException("Model has no hitbox set " + setName);
        if (modelSet.Length != shapes.Length) throw new InvalidDataException("Recorded hitbox count differs from the model's set");
        checkedFrames++;
        for (var i = 0; i < shapes.Length; i++)
        {
            var hitbox = modelSet[i]; var shape = shapes[i];
            var radius = new Vector3(hitbox.ShapeRadius);
            var (mins, maxs) = hitbox.ShapeType switch
            {
                Hitbox.HitboxShape.Sphere => (hitbox.MinBounds - radius, hitbox.MinBounds + radius),
                Hitbox.HitboxShape.Capsule => (Vector3.Min(hitbox.MinBounds, hitbox.MaxBounds) - radius, Vector3.Max(hitbox.MinBounds, hitbox.MaxBounds) + radius),
                _ => (hitbox.MinBounds, hitbox.MaxBounds),
            };
            var error = MathF.Max((mins - shape.Min).Length(), (maxs - shape.Max).Length());
            maxBoundsError = MathF.Max(maxBoundsError, error); checkedBoxes++;
            if (!(error <= 1e-3f)) throw new InvalidDataException("Recorded hitbox bounds differ from the model's hitbox " + i);
            var bone = Model.Skeleton.GetBoneIndex(hitbox.BoneName);
            if (bone < 0) throw new InvalidDataException("Hitbox bone is not in the skeleton: " + hitbox.BoneName);
            var world = Matrix4x4.CreateScale(shape.Scale) * Matrix4x4.CreateFromQuaternion(shape.Rotation) * Matrix4x4.CreateTranslation(shape.Position);
            if (hitboxPoses.TryGetValue(bone, out var existing) && checkedFrames == 1 && (existing.Translation - world.Translation).Length() > 1e-3f)
                throw new InvalidDataException("Two hitboxes on one bone carry different recorded frames");
            hitboxPoses[bone] = world;
        }
    }

    public void BeginFrame() => hitboxPoses.Clear();

    /// <summary>Recorded world pose of every bone in model order, one row of position, uniform scale and
    /// rotation (x, y, z, scale, qx, qy, qz, qw) per bone. Replaces the whole pose for this frame.</summary>
    public void SetWorldPose(System.Text.Json.JsonElement rows)
    {
        var count = Model.Skeleton.Bones.Length;
        if (rows.GetArrayLength() != count) throw new InvalidDataException("Recorded bone count differs from the model skeleton");
        worldPose ??= new Matrix4x4[count];
        var index = 0;
        foreach (var row in rows.EnumerateArray())
        {
            var v = row.EnumerateArray().Select(x => x.GetSingle()).ToArray();
            if (v.Length != 8 || v.Any(x => !float.IsFinite(x)) || !(v[3] > 0)) throw new InvalidDataException("Invalid recorded bone pose");
            worldPose[index++] = Matrix4x4.CreateScale(v[3])
                * Matrix4x4.CreateFromQuaternion(Quaternion.Normalize(new Quaternion(v[4], v[5], v[6], v[7])))
                * Matrix4x4.CreateTranslation(v[0], v[1], v[2]);
        }
    }

    public void GetBonePose(Span<Matrix4x4> bones)
    {
        poseReads++;
        if (worldPose is not null) { worldPose.CopyTo(bones); return; }
        if (bind is null) { bind = new Matrix4x4[Model.Skeleton.Bones.Length]; BindPoseModelObject.GetBindPose(Model, bind); }
        bind.CopyTo(bones);
        foreach (var (index, frame) in snapshotFrames) bones[index] = bind[index] * frame;
        foreach (var (index, world) in hitboxPoses) bones[index] = world;
    }
}

/// <summary>
/// Control-point bookkeeping on 21.1. Each system owns a 64-slot table; a slot exists once it was read or written
/// (materialized), and 21.1 remembers which slots particle functions wrote. Unprovided = materialized, not supplied
/// by the request and never written by a function. 21.1 has no read hook, so a read cannot be told from a write.
/// </summary>
static class ControlPointAudit
{
    public const string Policy = "agr_s2v211_function_write_audit_v1";
    static readonly FieldInfo Points = typeof(ParticleSystemState).GetField("controlPoints", BindingFlags.Instance | BindingFlags.NonPublic)
        ?? throw new InvalidOperationException("21.1 control-point table layout changed");
    static readonly FieldInfo Writes = typeof(ParticleSystemState).GetField("functionWrites", BindingFlags.Instance | BindingFlags.NonPublic)
        ?? throw new InvalidOperationException("21.1 function-write record layout changed");

    public static ControlPoint?[] Table(ParticleSystemState state) => (ControlPoint?[])Points.GetValue(state)!;

    static void Visit(ParticleSystemSimulation root, Action<ParticleSystemSimulation, string> visit, string path = "root")
    {
        visit(root, path);
        for (var i = 0; i < root.Children.Count; i++) Visit(root.Children[i], visit, path + "/" + i);
    }

    /// <summary>
    /// Materialized positional slots. A slot that only carries a bound particle snapshot (m_nSnapshotControlPoint)
    /// is reported separately: 21.1 creates it to look the snapshot up, it is not a missing transform input.
    /// </summary>
    public static (int[] Positional, int[] SnapshotCarriers) Indices(ParticleSystemSimulation root)
    {
        var indices = new SortedSet<int>(); var carriers = new SortedSet<int>(); var written = new SortedSet<int>();
        Visit(root, (s, _) =>
        {
            var t = Table(s.RenderState);
            for (var i = 0; i < t.Length; i++)
            {
                if (t[i] is not { } point) continue;
                indices.Add(i);
                if (point.Snapshot != null) carriers.Add(i);
                if (point.IsWritten) written.Add(i);
            }
        });
        carriers.ExceptWith(written);
        indices.ExceptWith(carriers);
        return (indices.ToArray(), carriers.ToArray());
    }

    /// <summary>Slots written by particle functions since the last host write; accumulate every tick, 21.1 forgets on host writes.</summary>
    public static void Accumulate(ParticleSystemSimulation root, SortedSet<int> authored, SortedSet<int> authoredRoot)
    {
        Visit(root, (s, path) =>
        {
            if (Writes.GetValue(s.RenderState) is not System.Collections.IDictionary writes) return;
            foreach (var key in writes.Keys) { authored.Add((int)key); if (path == "root") authoredRoot.Add((int)key); }
        });
    }
}

/// <summary>Effective per-system control points: the parent's visible set overlaid by this system's own slots.</summary>
static class RenderControlSnapshot
{
    public const string Policy = "agr_visible_control_points_v1";

    static SortedDictionary<int, ControlPoint> Visible(ParticleSystemState state)
    {
        var result = state.ParentSystem is { } parent ? Visible(parent) : new();
        var table = ControlPointAudit.Table(state);
        // A slot that only carries a bound snapshot holds no transform; it is listed as a snapshot carrier instead.
        for (var i = 0; i < table.Length; i++) if (table[i] is { } point && (point.Snapshot == null || point.IsWritten)) result[i] = point;
        return result;
    }

    public static object[] Capture(ParticleSystemState state) => Visible(state).Select(entry =>
    {
        var p = entry.Value.Position;
        if (!float.IsFinite(p.X) || !float.IsFinite(p.Y) || !float.IsFinite(p.Z)) throw new InvalidOperationException("Non-finite render control point");
        return (object)new { index = entry.Key, position = new[] { p.X, p.Y, p.Z } };
    }).ToArray();
}

static class ReplayLifecycle
{
    // Return true for a terminal immediate destruction. The caller keeps empty output frames afterwards.
    public static bool Destroy(ParticleSystemSimulation root, bool immediately)
    {
        if (immediately) { root.StopEmission(removeAllParticles: true, playEndCap: false); return true; }
        root.Stop();
        root.PlayEndCap();
        return false;
    }
}
