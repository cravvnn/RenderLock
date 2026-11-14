using System.Numerics;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveResourceFormat;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;

/// <summary>Bounded authored hitbox metadata; no simulation provider is implied.</summary>
static class ModelHitboxInventory
{
    static string Digest(Resource resource)
    {
        var input = resource.Reader?.BaseStream ?? throw new InvalidDataException("Missing resource bytes");
        if (!input.CanSeek || input.Length is <= 0 or > 256 * 1024 * 1024)
            throw new InvalidDataException("Compiled hitbox source exceeds hash bounds");
        var position = input.Position;
        try { input.Position = 0; return Convert.ToHexStringLower(SHA256.HashData(input)); }
        finally { input.Position = position; }
    }

    static void Relative(string name, string suffix)
    {
        if (string.IsNullOrEmpty(name) || !name.EndsWith(suffix, StringComparison.Ordinal)
            || name.Contains("..") || name.Contains(':') || name.Contains('\\') || name.StartsWith('/'))
            throw new InvalidDataException("Expected a relative installed resource: " + suffix);
    }

    public static void Run(string gameinfo, string name, string destination)
    {
        Relative(name, ".vmdl");
        if (File.Exists(destination)) throw new InvalidDataException("Choose a new hitbox inventory output");
        using var loader = new GameFileLoader(null, gameinfo, NullLogger.Instance);
        using var resource = loader.LoadFileCompiled(name) ?? throw new FileNotFoundException(name);
        var model = resource.DataBlock as Model ?? throw new InvalidDataException("Expected a model resource");
        var sources = new List<object> { new { resource = name, sha256 = Digest(resource), route = "model" } };
        if (model.HitboxSets.Count == 0)
        {
            var meshes = model.GetReferenceMeshNamesAndLoD().ToArray();
            if (meshes.Length > 32) throw new InvalidDataException("Excessive external mesh count");
            foreach (var reference in meshes)
            {
                Relative(reference.MeshName, ".vmesh");
                using var meshResource = loader.LoadFileCompiled(reference.MeshName)
                    ?? throw new FileNotFoundException(reference.MeshName);
                var mesh = meshResource.DataBlock as Mesh ?? throw new InvalidDataException("Expected a mesh resource");
                sources.Add(new { resource = reference.MeshName, sha256 = Digest(meshResource), route = "external_mesh" });
                model.SetExternalMeshData(mesh);
                if (model.HitboxSets.Count > 0) break;
            }
        }
        var names = model.Skeleton.Bones.Select(bone => bone.Name).ToArray();
        if (names.Length is < 1 or > 32767 || names.Distinct(StringComparer.Ordinal).Count() != names.Length
            || model.HitboxSets.Count > 128 || model.HitboxSets.Values.Sum(boxes => boxes.Length) > 4096)
            throw new InvalidDataException("Hitbox metadata exceeds bounded unique skeleton/set limits");
        static float[] V(Vector3 value)
        {
            var result = new[] { value.X, value.Y, value.Z };
            if (result.Any(item => !float.IsFinite(item))) throw new InvalidDataException("Nonfinite hitbox bound");
            return result;
        }
        var sets = model.HitboxSets.Select((entry, index) => new {
            file_order_index = index, name = entry.Key, boxes = entry.Value.Select((box, boxIndex) => {
                if (!float.IsFinite(box.ShapeRadius)) throw new InvalidDataException("Nonfinite hitbox radius");
                return new { index = boxIndex, name = box.Name, bone = box.BoneName,
                    model_bone_index = Array.IndexOf(names, box.BoneName), min = V(box.MinBounds), max = V(box.MaxBounds),
                    shape = box.ShapeType.ToString(), radius = box.ShapeRadius,
                    translation_only = box.TranslationOnly, group = box.GroupId, surface = box.SurfaceProperty };
            }).ToArray()
        }).ToArray();
        using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        JsonSerializer.Serialize(output, new { schema_version = 1, model = name, sources,
            model_bone_names = names, hitbox_sets = sets,
            scope = "Authored model/mesh hitbox metadata in file order only; native selected-set routing, hitbox sampling, current/previous poses and LockToBone are not qualified" },
            new JsonSerializerOptions { WriteIndented = true });
        output.Flush(true);
        Console.WriteLine($"Inventoried {sets.Length} hitbox sets/{sets.Sum(set => set.boxes.Length)} boxes/{names.Length} model bones; no geometry output");
    }
}
