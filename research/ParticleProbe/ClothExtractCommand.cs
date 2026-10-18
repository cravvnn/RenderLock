using System.Collections;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;

static class ClothExtractCommand
{
    public static void Run(string gameinfo, string resourceName, string destination)
    {
        if (!resourceName.EndsWith(".vmdl", StringComparison.Ordinal) || resourceName.Contains("..")
            || resourceName.Contains(':') || resourceName.StartsWith('/'))
            throw new InvalidDataException("Expected a relative compiled model resource");
        var output = Path.GetFullPath(destination);
        if (!Directory.Exists(output) || Directory.EnumerateFileSystemEntries(output).Any())
            throw new InvalidDataException("Cloth extraction requires a fresh empty output directory");
        using var loader = new GameFileLoader(null, gameinfo, NullLogger.Instance);
        using var resource = loader.LoadFileCompiled(resourceName) ?? throw new FileNotFoundException(resourceName);
        var model = (Model)resource.DataBlock!;
        var extract = new ModelExtract(resource, loader) { ExtractAnimations = false };
        extract.AnimationsToExtract.Clear();
        extract.RenderMeshesToExtract.RemoveAll(mesh => !model.LodInfo.IsMeshInLevel(mesh.Index, 0));
        using var content = extract.ToContentFile();
        // The exact pinned extractor exposes cloth internally. Reflect only this
        // metadata; fail if its contract changes rather than guessing file names.
        var cloth = typeof(ModelExtract).GetProperty("Cloth", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(extract)!;
        var proxies = (IEnumerable)cloth.GetType().GetProperty("ProxyMeshes", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(cloth)!;
        var proxyNames = new HashSet<string>(StringComparer.Ordinal);
        foreach (var proxy in proxies)
            proxyNames.Add(Path.GetFileName((string)proxy!.GetType().GetProperty("FileName")!.GetValue(proxy)!));
        var renderNames = extract.RenderMeshesToExtract.Select(m => Path.GetFileName(m.FileName)).ToHashSet(StringComparer.Ordinal);
        var selected = content.SubFiles.Where(s => proxyNames.Contains(s.FileName) || renderNames.Contains(s.FileName)).ToArray();
        if (selected.Length > 48 || selected.Select(s => s.FileName).Distinct().Count() != selected.Length)
            throw new InvalidDataException("Ambiguous or excessive decompiled cloth file set");
        var files = new List<object>(); long total = 0;
        foreach (var file in selected)
        {
            if (file.FileName != Path.GetFileName(file.FileName) || !file.FileName.EndsWith(".dmx"))
                throw new InvalidDataException("Unsafe decompiled cloth filename");
            var method = file.Extract ?? throw new InvalidDataException("Missing cloth DMX extractor");
            var bytes = method() ?? throw new InvalidDataException("Empty cloth DMX extraction"); total += bytes.Length;
            if (bytes.Length > 64 * 1024 * 1024 || total > 128 * 1024 * 1024)
                throw new InvalidDataException("Decompiled cloth exceeds size bound");
            File.WriteAllBytes(Path.Combine(output, file.FileName), bytes);
            files.Add(new { path = file.FileName, bytes = bytes.Length, sha256 = Convert.ToHexStringLower(SHA256.HashData(bytes)) });
        }
        File.WriteAllText(Path.Combine(output, "model.vmdl"), System.Text.Encoding.UTF8.GetString(content.Data!));
        var physics = model.GetEmbeddedPhys();
        using var referencedPhysics = model.GetReferencedPhysNames()?.FirstOrDefault() is { } physName
            ? loader.LoadFileCompiled(physName) : null;
        if (referencedPhysics?.DataBlock is PhysAggregateData externalPhysics) physics = externalPhysics;
        var manifest = new { schema_version = 1, resource = resourceName, animation_files = 0,
            fe_node_count = physics?.FeModel?.NodeCount ?? 0,
            hitbox_sets = model.HitboxSets.ToDictionary(p => p.Key, p => p.Value.Select(h => new {
                name = h.Name, bone = h.BoneName, shape = h.ShapeType.ToString(), radius = h.ShapeRadius,
                minimum = new[] { h.MinBounds.X, h.MinBounds.Y, h.MinBounds.Z },
                maximum = new[] { h.MaxBounds.X, h.MaxBounds.Y, h.MaxBounds.Z }, translation_only = h.TranslationOnly }).ToArray()),
            proxy_files = proxyNames.Order().ToArray(),
            render_meshes = extract.RenderMeshesToExtract.Select(m => new { file = Path.GetFileName(m.FileName), name = m.Name }).ToArray(),
            files };
        File.WriteAllText(Path.Combine(output, "cloth-extraction.json"), JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"Extracted {proxyNames.Count} cloth proxies and {renderNames.Count} render meshes; no animation files");
    }
}
