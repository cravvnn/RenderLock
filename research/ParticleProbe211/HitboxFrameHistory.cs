using System.Collections.Immutable;
using System.IO;
using System.Numerics;

namespace ValveResourceFormat.Particles;

/// <summary>Value-only Source-world hitbox record. No mutable pose arrays.</summary>
public readonly record struct AuthoredHitboxShape(Vector3 Min, Vector3 Max,
    Vector3 Position, Quaternion Rotation, float Scale, float Weight, int Mapping);

/// <summary>
/// Export-side CP history, following qualified native CP-cache time/swap rules.
/// Single-threaded; caller supplies validated immutable snapshots and a stable
/// replay/entity/model binding. No engine model lookup or implicit rebind.
/// </summary>
public sealed class HitboxFrameHistory
{
    /// <summary>Immutable current and previous snapshots for one CP query.</summary>
    public sealed record Frame(float Time, string Binding,
        ImmutableArray<AuthoredHitboxShape> Current,
        ImmutableArray<AuthoredHitboxShape> Previous);

    private readonly Dictionary<int, Frame> frames = new();

    /// <summary>Require a stable binding and monotonically advance one CP's history.</summary>
    public Frame Query(int controlPoint, float time, string binding,
        ImmutableArray<AuthoredHitboxShape> shapes)
    {
        if (controlPoint is < 0 or > 63 || !float.IsFinite(time) || time < 0
            || string.IsNullOrWhiteSpace(binding) || shapes.IsDefaultOrEmpty || shapes.Length > 4096)
            throw new InvalidDataException("Invalid explicit hitbox frame input");
        if (frames.TryGetValue(controlPoint, out var prior))
        {
            if (prior.Binding != binding)
                throw new InvalidDataException("Hitbox CP binding changed during export");
            if (time < prior.Time)
                throw new InvalidDataException("Hitbox history cannot rewind; create a fresh simulation");
            // Native CP cache is shared by consumers. First query in a frame
            // determines its set/poses; a repeated query does not advance it.
            if (time == prior.Time) return prior;
        }
        foreach (var shape in shapes)
        {
            static bool Finite(Vector3 v) => float.IsFinite(v.X) && float.IsFinite(v.Y) && float.IsFinite(v.Z);
            if (!Finite(shape.Min) || !Finite(shape.Max) || !Finite(shape.Position)
                || shape.Min.X > shape.Max.X || shape.Min.Y > shape.Max.Y || shape.Min.Z > shape.Max.Z
                || !float.IsFinite(shape.Scale) || shape.Scale <= 0 || !float.IsFinite(shape.Weight) || shape.Weight < 0
                || !float.IsFinite(shape.Rotation.LengthSquared()) || MathF.Abs(shape.Rotation.LengthSquared() - 1) > 2e-4f
                || shape.Mapping != -1)
                throw new InvalidDataException("Invalid authored CP hitbox shape");
        }
        var next = new Frame(time, binding, shapes,
            prior?.Current ?? ImmutableArray<AuthoredHitboxShape>.Empty);
        frames[controlPoint] = next;
        return next;
    }
}
