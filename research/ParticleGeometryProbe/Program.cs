using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;

// Geometry only. Material decoding stays in the separately pinned ParticleProbe.
// No retries, cache publication, game process access or Blender reconstruction.
const string Policy = "agr_particle_model_morph_v1";
try
{
    if (args.Length == 1 && args[0] == "--capabilities")
    {
        Console.WriteLine(JsonSerializer.Serialize(new { policy = Policy,
            upstream_revision = "42743fb93030c552f834fabb30708dad84d0cbf5",
            patches = new[] { "vrf-skeletonless-morph-animation.patch" },
            material_exported = false }));
        return 0;
    }
    if (args.Length != 4 || args[0] != "--particle-model-v1")
        throw new ArgumentException("Expected --particle-model-v1 gameinfo relative-model.vmdl empty-output-directory");
    var name = args[2];
    if (name.Length > 512 || !name.EndsWith(".vmdl", StringComparison.Ordinal)
        || name.Contains(':') || name.Contains('\\') || name.Any(char.IsControl)
        || name.Split('/').Any(p => p is "" or "." or ".."))
        throw new InvalidDataException("Expected a relative particle model resource");
    var destination = Path.GetFullPath(args[3]);
    if (File.Exists(destination) || (Directory.Exists(destination)
        && Directory.EnumerateFileSystemEntries(destination).Any()))
        throw new InvalidDataException("Particle model output must be empty");

    using var loader = new GameFileLoader(null, args[1], NullLogger.Instance);
    using var resource = loader.LoadFileCompiled(name) ?? throw new FileNotFoundException(name);
    var model = resource.DataBlock as Model ?? throw new InvalidDataException("Expected a model");
    var stream = resource.Reader!.BaseStream;
    if (stream.Length > 64 * 1024 * 1024)
        throw new InvalidDataException("Particle model source bound exceeded");
    stream.Position = 0;
    var sourceHash = Convert.ToHexStringLower(SHA256.HashData(stream));
    var animations = model.GetAllAnimations(loader).Take(2).Select((a, i) => new {
        index = i, name = a.Name, fps = a.Fps, frames = a.FrameCount,
        cycle_frames = a.CycleFrames, has_flex_data = a.HasFlexData }).ToArray();
    if (model.Skeleton.Bones.Length != 0 || model.MeshGroups.Names.Count > 1
        || model.MeshGroups.BodyGroups.Count != 0 || animations.Length != 1
        || !animations[0].has_flex_data || !float.IsFinite(animations[0].fps)
        || animations[0].fps <= 0 || animations[0].fps > 1000
        || animations[0].frames < 2 || animations[0].frames > 4096
        || animations[0].cycle_frames != animations[0].frames - 1)
        throw new InvalidDataException("Expected one bounded unskinned morph animation without body selectors");

    Directory.CreateDirectory(destination);
    var geometry = Path.Combine(destination, "model.glb");
    var exporter = new GltfModelExporter(loader) { ExportMaterials = false, ExportAnimations = true };
    exporter.Export(resource, geometry);
    if (new FileInfo(geometry).Length > 64 * 1024 * 1024)
        throw new InvalidDataException("Particle model geometry bound exceeded");
    // Written last. Callers still validate geometry, hashes and animation metadata
    // before their existing transactional conversion cache may publish anything.
    File.WriteAllText(Path.Combine(destination, "model.json"), JsonSerializer.Serialize(new {
        schema_version = 1, policy = Policy, resource = name, source_sha256 = sourceHash,
        source_blocks = resource.Blocks.Select(b => b.Type.ToString()).ToArray(),
        bones = model.Skeleton.Bones.Length, animations, mesh_groups = model.MeshGroups.Names,
        body_groups = model.MeshGroups.BodyGroups.Count, geometry_file = "model.glb", material_exported = false,
        scope = "Installed unskinned geometry and authored morph animation only; no particle placement or native playback qualification"
    }, new JsonSerializerOptions { WriteIndented = true }));
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine($"Particle geometry export failed: {error.Message}");
    return 1;
}
