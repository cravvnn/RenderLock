using System.Collections;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveResourceFormat;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

static class ClothExtractCommand
{
    public static void InventoryClipEvents(string gameinfo, string clipsJson, string destination)
    {
        if (new FileInfo(clipsJson).Length > 1024 * 1024 || File.Exists(destination))
            throw new InvalidDataException("Clip inventory requires bounded input and a new output file");
        var clips = JsonSerializer.Deserialize<string[]>(File.ReadAllText(clipsJson))
            ?? throw new InvalidDataException("Expected a clip resource array");
        if (clips.Length is < 1 or > 256 || clips.Distinct(StringComparer.Ordinal).Count() != clips.Length
            || clips.Any(name => string.IsNullOrEmpty(name) || !name.EndsWith(".vnmclip", StringComparison.Ordinal)
                || name.Contains("..") || name.Contains(':') || name.StartsWith('/') || name.Contains('\\')))
            throw new InvalidDataException("Expected unique relative animation clip resource names");
        using var loader = new GameFileLoader(null, gameinfo, NullLogger.Instance);
        var rows = new List<object>();
        foreach (var name in clips)
        {
            using var resource = loader.LoadFileCompiled(name) ?? throw new FileNotFoundException(name);
            var clip = resource.DataBlock as ValveResourceFormat.ResourceTypes.ModelAnimation2.AnimationClip
                ?? throw new InvalidDataException("Resource is not an animation clip: " + name);
            if (clip.Events.Length > 4096)
                throw new InvalidDataException("Excessive animation clip event count");
            var events = clip.Events.Select(value => new { class_name = value.ClassName,
                start_cycle = value.StartCycle, start_seconds = value.StartTime,
                duration_seconds = value.Duration, sync_id = value.SyncId,
                raw_kv3 = value.Data.ToKV3String() }).ToArray();
            if (events.Sum(value => value.raw_kv3.Length) > 1024 * 1024)
                throw new InvalidDataException("Clip event metadata exceeds inventory bound");
            rows.Add(new { resource = name, skeleton = clip.SkeletonName,
                duration_seconds = clip.Duration, events });
        }
        using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        JsonSerializer.Serialize(output, new { schema_version = 1, clips = rows,
            scope = "Raw installed animation clip event inventory only; graph triggering, filtering and activation history are not inferred" },
            new JsonSerializerOptions { WriteIndented = true });
        output.Flush(true);
        Console.WriteLine($"Inventoried {rows.Count} clip event records; no pose/model/geometry output");
    }

    public static void Inventory(string gameinfo, string modelsJson, string destination)
    {
        if (new FileInfo(modelsJson).Length > 1024 * 1024 || File.Exists(destination))
            throw new InvalidDataException("Inventory requires bounded model input and a new output file");
        var models = JsonSerializer.Deserialize<string[]>(File.ReadAllText(modelsJson))
            ?? throw new InvalidDataException("Expected a model resource array");
        if (models.Length is < 1 or > 128 || models.Distinct(StringComparer.Ordinal).Count() != models.Length
            || models.Any(name => string.IsNullOrEmpty(name) || !name.EndsWith(".vmdl", StringComparison.Ordinal)
                || name.Contains("..") || name.Contains(':') || name.StartsWith('/') || name.Contains('\\')))
            throw new InvalidDataException("Expected unique relative compiled-model resource names");
        using var loader = new GameFileLoader(null, gameinfo, NullLogger.Instance);
        var rows = new List<object>();
        long totalMetadata = 0;
        foreach (var name in models)
        {
            try
            {
                using var resource = loader.LoadFileCompiled(name) ?? throw new FileNotFoundException(name);
                var model = (Model)resource.DataBlock!;
                var sourceSha256 = ResourceSha256(resource);
                var physics = model.GetEmbeddedPhys();
                var references = model.GetReferencedPhysNames()?.ToArray() ?? [];
                if (references.Length > 1 || references.Any(value => string.IsNullOrEmpty(value)
                    || !value.EndsWith(".vphys", StringComparison.Ordinal) || value.Contains("..")
                    || value.Contains(':') || value.StartsWith('/') || value.Contains('\\')))
                    throw new InvalidDataException("Unqualified physics reference inventory");
                var physicsName = references.FirstOrDefault();
                using var referenced = physicsName is not null
                    ? loader.LoadFileCompiled(physicsName) ?? throw new FileNotFoundException(physicsName) : null;
                if (referenced?.DataBlock is PhysAggregateData externalPhysics) physics = externalPhysics;
                else if (referenced is not null) throw new InvalidDataException("Referenced resource is not physics");
                var physicsSha256 = referenced is null ? null : ResourceSha256(referenced);
                var fe = physics?.FeModel;
                var keyvalues = model.Data.GetSubCollection("m_modelInfo").GetStringProperty("m_keyValueText");
                if (keyvalues?.Length > 1024 * 1024)
                    throw new InvalidDataException("Model key-value metadata exceeds inventory bound");
                var counts = fe?.Data.Children.Where(pair => pair.Value.IsArray)
                    .ToDictionary(pair => pair.Key!, pair => pair.Value.Count, StringComparer.Ordinal);
                var rawFe = fe?.Data.ToKV3String();
                var exactFe = fe is null ? (JsonElement?)null : RawFeJson(fe.Data);
                totalMetadata += (rawFe?.Length ?? 0) + (exactFe?.GetRawText().Length ?? 0) + (keyvalues?.Length ?? 0);
                if (rawFe?.Length > 8 * 1024 * 1024 || totalMetadata > 128 * 1024 * 1024)
                    throw new InvalidDataException("Raw FE inventory metadata exceeds supported bound");
                rows.Add(new { resource = name, status = fe is null ? "not_applicable" : "inventoried",
                    fe_node_count = fe?.NodeCount ?? 0, static_node_count = fe?.StaticNodes ?? 0,
                    dynamic_node_flags = fe?.DynamicNodeFlags ?? 0,
                    static_node_flags = fe?.StaticNodeFlags ?? 0,
                    array_counts = counts, raw_fe_kv3 = rawFe, raw_fe_json = exactFe,
                    numeric_encoding = "binary_kv3_roundtrip_v1", source_sha256 = sourceSha256,
                    referenced_physics = physicsName, referenced_physics_sha256 = physicsSha256,
                    model_keyvalue_text = keyvalues,
                    scope = "Raw FE declarations, array inventory and model key-value metadata; no cloth support or activation state inferred" });
            }
            catch (Exception ex)
            {
                rows.Add(new { resource = name, status = "failed", error = ex.Message });
            }
        }
        using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        JsonSerializer.Serialize(output, new { schema_version = 1, models = rows,
            scope = "Read-only installed PHYS inventory; no model/DMX/render files extracted or cached" },
            new JsonSerializerOptions { WriteIndented = true });
        output.Flush(true);
        Console.WriteLine($"Inventoried {rows.Count} installed model physics resources; no geometry files written");
    }

    static JsonElement RawFeJson(ValveKeyValue.KVObject data)
    {
        using var buffer = new MemoryStream();
        using (var writer = new Utf8JsonWriter(buffer))
        {
            int remaining = 2_000_000;
            FePhysicsJsonCommand.WriteValue(writer, data, 0, ref remaining);
            writer.Flush();
        }
        if (buffer.Length > 16 * 1024 * 1024)
            throw new InvalidDataException("Typed FE inventory metadata exceeds supported bound");
        using var parsed = JsonDocument.Parse(buffer.ToArray());
        return parsed.RootElement.Clone();
    }

    static string ResourceSha256(Resource resource)
    {
        var input = resource.Reader?.BaseStream ?? throw new InvalidDataException("Compiled resource stream unavailable");
        if (!input.CanSeek || input.Length is <= 0 or > 256 * 1024 * 1024)
            throw new InvalidDataException("Compiled resource exceeds inventory hash bounds");
        var position = input.Position;
        try
        {
            input.Position = 0;
            return Convert.ToHexStringLower(SHA256.HashData(input));
        }
        finally { input.Position = position; }
    }

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
        var physics = model.GetEmbeddedPhys();
        using var referencedPhysics = model.GetReferencedPhysNames()?.FirstOrDefault() is { } physName
            ? loader.LoadFileCompiled(physName) : null;
        if (referencedPhysics?.DataBlock is PhysAggregateData externalPhysics) physics = externalPhysics;
        var nodeCount = physics?.FeModel?.NodeCount ?? 0;
        var extract = new ModelExtract(resource, loader) { ExtractAnimations = false };
        extract.AnimationsToExtract.Clear();
        extract.RenderMeshesToExtract.RemoveAll(mesh => !model.LodInfo.IsMeshInLevel(mesh.Index, 0));
        using var content = extract.ToContentFile();
        // The exact pinned extractor exposes cloth internally. Reflect only this
        // metadata; fail if its contract changes rather than guessing file names.
        var cloth = typeof(ModelExtract).GetProperty("Cloth", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(extract)!;
        var proxies = (IEnumerable)cloth.GetType().GetProperty("ProxyMeshes", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(cloth)!;
        var proxyNames = new HashSet<string>(StringComparer.Ordinal);
        var proxyNodes = new List<object>();
        foreach (var proxy in proxies)
        {
            var descriptor = proxy ?? throw new InvalidDataException("Missing cloth proxy descriptor");
            var filename = Path.GetFileName((string)descriptor.GetType().GetProperty("FileName")!.GetValue(descriptor)!);
            var geometry = descriptor.GetType().GetProperty("Proxy")!.GetValue(descriptor)
                ?? throw new InvalidDataException("Missing cloth proxy geometry");
            var indices = ((int[])geometry.GetType().GetProperty("NodeIndices")!.GetValue(geometry)!).ToArray();
            var faces = (IEnumerable)geometry.GetType().GetProperty("Faces")!.GetValue(geometry)!;
            if (indices.Length is < 1 or > 1_000_000 || indices.Any(n => n < -1 || n >= nodeCount))
                throw new InvalidDataException("Cloth proxy node identity outside FE model");
            foreach (var face in faces)
                foreach (var vertex in (int[])face!)
                    if (vertex < 0 || vertex >= indices.Length || indices[vertex] < 0)
                        throw new InvalidDataException("Faced cloth proxy vertex lacks an FE node identity");
            if (!proxyNames.Add(filename))
                throw new InvalidDataException("Duplicate cloth proxy file identity");
            proxyNodes.Add(new { file = filename, node_indices = indices });
        }
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
        var modeldoc = content.Data ?? throw new InvalidDataException("Missing cloth modeldoc metadata");
        if (modeldoc.Length > 8 * 1024 * 1024 || total + modeldoc.Length > 128 * 1024 * 1024)
            throw new InvalidDataException("Decompiled cloth metadata exceeds size bound");
        File.WriteAllBytes(Path.Combine(output, "model.vmdl"), modeldoc);
        var manifest = new { schema_version = 1, resource = resourceName, animation_files = 0,
            fe_node_count = physics?.FeModel?.NodeCount ?? 0,
            hitbox_sets = model.HitboxSets.ToDictionary(p => p.Key, p => p.Value.Select(h => new {
                name = h.Name, bone = h.BoneName, shape = h.ShapeType.ToString(), radius = h.ShapeRadius,
                minimum = new[] { h.MinBounds.X, h.MinBounds.Y, h.MinBounds.Z },
                maximum = new[] { h.MaxBounds.X, h.MaxBounds.Y, h.MaxBounds.Z }, translation_only = h.TranslationOnly }).ToArray()),
            proxy_files = proxyNames.Order().ToArray(),
            proxy_nodes = proxyNodes,
            modeldoc = new { path = "model.vmdl", bytes = modeldoc.Length,
                sha256 = Convert.ToHexStringLower(SHA256.HashData(modeldoc)) },
            render_meshes = extract.RenderMeshesToExtract.Select(m => new { file = Path.GetFileName(m.FileName), name = m.Name }).ToArray(),
            files };
        File.WriteAllText(Path.Combine(output, "cloth-extraction.json"), JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"Extracted {proxyNames.Count} cloth proxies and {renderNames.Count} render meshes; no animation files");
    }
}
