#if AGR_SIMULATION_SNAPSHOT
using System.Numerics;
using System.Text.Json;
using System.Security.Cryptography;
using ValveResourceFormat.Particles.Utils;

static class SnapshotSkinningOracle
{
    public static void Check(string input, string output)
    {
        using var doc = JsonDocument.Parse(File.ReadAllBytes(input));
        int initialized = 0, updated = 0, refusals = 0;
        float maximumError = 0;
        Vector3 Vector(JsonElement value)
        {
            var v = value.EnumerateArray().Select(x => x.GetSingle()).ToArray();
            if (v.Length != 3) throw new Exception("Bad vector fixture");
            return new(v[0], v[1], v[2]);
        }
        SnapshotSkinningMatrix? Matrix(JsonElement value) => value.ValueKind == JsonValueKind.Null
            ? null : new SnapshotSkinningMatrix(value.EnumerateArray().Select(x => x.GetSingle()).ToArray());
        void Compare((Vector3 Position, Vector3 Previous) actual, JsonElement expected)
        {
            foreach (var pair in new[] { (actual.Position, Vector(expected.GetProperty("position"))),
                (actual.Previous, Vector(expected.GetProperty("previous"))) })
            {
                var delta = Vector3.Abs(pair.Item1 - pair.Item2);
                float error = Math.Max(delta.X, Math.Max(delta.Y, delta.Z));
                if (!float.IsFinite(error) || error > 0.0001f) throw new Exception($"Native snapshot position mismatch: {error}");
                maximumError = Math.Max(maximumError, error);
            }
        }
        foreach (var row in doc.RootElement.GetProperty("initializers").EnumerateArray())
        {
            Compare(SnapshotSkinningMath.InitializePositionPair(Vector(row.GetProperty("rest")),
                Matrix(row.GetProperty("matrix")), row.GetProperty("rigid").GetBoolean()), row.GetProperty("expected"));
            initialized++;
        }
        foreach (var row in doc.RootElement.GetProperty("updates").EnumerateArray())
        {
            Compare(SnapshotSkinningMath.UpdatePositionPair(Vector(row.GetProperty("rest")),
                Vector(row.GetProperty("initial_previous")), Matrix(row.GetProperty("matrix")),
                Vector(row.GetProperty("before_position")), Vector(row.GetProperty("before_previous"))), row.GetProperty("expected"));
            updated++;
        }
        void Refuse(Action action)
        {
            try { action(); }
            catch (ArgumentException) { refusals++; return; }
            throw new Exception("Invalid snapshot kernel input accepted");
        }
        Refuse(() => _ = new SnapshotSkinningMatrix(new float[11]));
        Refuse(() => _ = new SnapshotSkinningMatrix(new float[13]));
        var identity = new float[] { 1,0,0,0, 0,1,0,0, 0,0,1,0 };
        foreach (float bad in new[] { float.NaN, float.PositiveInfinity, float.NegativeInfinity })
        {
            var matrix = (float[])identity.Clone(); matrix[11] = bad;
            Refuse(() => _ = new SnapshotSkinningMatrix(matrix));
            var vector = new Vector3(bad, 0, 0);
            Refuse(() => SnapshotSkinningMath.InitializePositionPair(vector, null, true));
            Refuse(() => SnapshotSkinningMath.UpdatePositionPair(Vector3.Zero, vector, null, Vector3.Zero, Vector3.Zero));
        }
        var large = new SnapshotSkinningMatrix(new float[] { float.MaxValue,0,0,0, 0,1,0,0, 0,0,1,0 });
        Refuse(() => large.TransformPoint(new Vector3(2, 0, 0)));
        var regular = new SnapshotSkinningMatrix(identity);
        Refuse(() => SnapshotSkinningMath.UpdatePositionPair(new Vector3(float.MaxValue,0,0),
            new Vector3(float.MaxValue,0,0), regular, Vector3.Zero, Vector3.Zero));
        File.WriteAllText(output, JsonSerializer.Serialize(new { status = "PASS", native_initializer_rows = initialized,
            native_update_rows = updated, maximum_position_error_source_units = maximumError,
            invalid_input_refusals = refusals, input_sha256 = Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(input))),
            scope = "Prepared actual C# position kernels vs native initializer and rigid-update outputs. Not a CP/model provider, emission scheduler, normal/radius implementation or full effect." },
            new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"Snapshot position kernels PASS: {initialized} initializers, {updated} updates");
    }
}
#endif
