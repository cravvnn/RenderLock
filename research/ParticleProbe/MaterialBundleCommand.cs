using System.Numerics;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using SharpGLTF.Schema2;
using ValveResourceFormat;
using ValveResourceFormat.CompiledShader;
using ValveResourceFormat.IO;
using VMaterial = ValveResourceFormat.ResourceTypes.Material;
using VTexture = ValveResourceFormat.ResourceTypes.Texture;

// A bounded material-only adapter to the pinned VRF glTF conversion. No model,
// mesh, skeleton, skin-index override or implicit game-file search is involved.
internal static class MaterialBundleCommand
{
    internal const string ParserSha256 = "cbc14ee90f4fa7481448721c6e0a66a1c153ccd191f9c1deb977c3abb6466cd6";
    private const long MaxBytes = 256L * 1024 * 1024;

    internal static void Run(string requestPath, string outputPath, string reportPath)
    {
        var requestBytes = ReadBounded(requestPath, 2 * 1024 * 1024);
        using var request = JsonDocument.Parse(requestBytes);
        var root = request.RootElement;
        if (root.GetProperty("schema_version").GetInt32() != 1)
            throw new InvalidDataException("Expected material-bundle request schema1");
        if (!Path.IsPathFullyQualified(outputPath) || !Path.IsPathFullyQualified(reportPath))
            throw new InvalidDataException("Absolute material bundle output paths required");
        var parserPath = typeof(Resource).Assembly.Location;
        if (Digest(File.ReadAllBytes(parserPath)) != ParserSha256)
            throw new InvalidDataException("Material-only exporter requires the qualified Safe7 parser assembly");
        if (File.Exists(outputPath) || File.Exists(reportPath))
            throw new IOException("Material bundle outputs must be new files");
        using var loader = new ExactLoader(root);
        var inputs = root.GetProperty("materials").EnumerateArray().Select(x => x.GetString()!).ToArray();
        if (inputs.Length is < 1 or > 64 || inputs.Distinct().Count() != inputs.Length)
            throw new InvalidDataException("Expected1..64 distinct exact material resources");
        var model = ModelRoot.CreateModel();
        var converted = new List<object>();
        // A shared exporter keys texture caches by basename. Reject ambiguous
        // basenames explicitly rather than binding an unrelated image silently.
        var basenames = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var name in inputs)
        {
            if (!name.EndsWith(".vmat_c", StringComparison.Ordinal))
                throw new InvalidDataException("Compiled VMAT identity required");
            using var resource = loader.LoadFile(name)!;
            if (resource.DataBlock is not VMaterial material)
                throw new InvalidDataException("Typed material DATA required");
            var shaders = loader.LoadShader(material.ShaderName)!;
            foreach (var (parameter, texture) in material.TextureParams)
            {
                if (!shaders.Features!.VariableDescriptions.Any(x => x.Name == parameter))
                    throw new InvalidDataException($"Unqualified shader texture parameter: {parameter}");
                var basename = Path.GetFileName(texture);
                if (basenames.TryGetValue(basename, out var previous) && previous != texture)
                    throw new InvalidDataException("Ambiguous source texture basenames");
                basenames[basename] = texture;
            }
            var exporter = new GltfModelExporter(loader) { ExportMaterials = true, AdaptTextures = true,
                ExportExtras = true, SatelliteImages = false };
            var binding = BindingFlags.Instance | BindingFlags.NonPublic;
            var method = typeof(GltfModelExporter).GetMethod("GenerateGLTFMaterialFromRenderMaterial", binding,
                [typeof(SharpGLTF.Schema2.Material), typeof(VMaterial), typeof(ModelRoot), typeof(Vector4)])
                ?? throw new MissingMethodException("Pinned material exporter signature changed");
            var tasksField = typeof(GltfModelExporter).GetField("TextureExportingTasks", binding)
                ?? throw new MissingFieldException("Pinned texture task contract changed");
            // Disable the generic missing-shader fallback. Shader resolution is
            // from authenticated explicit files only, never a game search path.
            var providerField = typeof(GltfModelExporter).GetField("shaderDataProvider", binding)
                ?? throw new MissingFieldException("Pinned shader provider contract changed");
            providerField.SetValue(exporter, new ShaderDataProvider(loader, useFallback: false));
            var target = model.CreateMaterial(Path.GetFileNameWithoutExtension(name));
            method.Invoke(exporter, [target, material, model, Vector4.One]);
            // Always observe every task, including tasks already faulted. The
            // upstream wait helper skips completed faults before clearing them.
            if (tasksField.GetValue(exporter) is not List<Task> tasks)
                throw new InvalidDataException("Pinned texture task list changed");
            Task.WhenAll(tasks).GetAwaiter().GetResult();
            converted.Add(new { resource = name, material_index = target.LogicalIndex,
                source_sha256 = loader.SourceSha(name), shader = material.ShaderName });
        }
        if (model.LogicalMeshes.Count != 0 || model.LogicalNodes.Count != 0 || model.LogicalSkins.Count != 0)
            throw new InvalidDataException("Material bundle unexpectedly contains geometry or a rig");
        var imageBytes = model.LogicalImages.Sum(x => (long)x.Content.Content.Length);
        if (imageBytes > MaxBytes) throw new InvalidDataException("Material bundle image budget exceeded");
        // SharpGLTF preserves unused materials/images in its logical collections.
        // The consumer binds exact resources; it never identifies them by names.
        model.SaveGLB(outputPath);
        var resultBytes = ReadBounded(outputPath, MaxBytes);
        var report = new { schema_version = 1, status = "converted_materials_only", policy = "exact_resources_v1",
            request_sha256 = Digest(requestBytes), parser_sha256 = ParserSha256,
            bundle_sha256 = Digest(resultBytes), bundle_bytes = resultBytes.Length,
            materials = converted, images = model.LogicalImages.Count, image_bytes = imageBytes,
            loaded_resources = loader.LoadedResources.Order().ToArray(), loaded_shaders = loader.LoadedShaders.Order().ToArray(),
            source_files = loader.Identities };
        File.WriteAllText(reportPath, JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"Converted {inputs.Length} exact materials; {model.LogicalImages.Count} embedded images; no geometry");
    }

    private static string Digest(byte[] data) => Convert.ToHexStringLower(SHA256.HashData(data));
    private static byte[] ReadBounded(string path, long limit)
    {
        if (!Path.IsPathFullyQualified(path)) throw new InvalidDataException("Absolute source/output paths required");
        using var input = File.OpenRead(path);
        if (input.Length is < 1 || input.Length > limit) throw new InvalidDataException("Source file exceeds byte bound");
        var bytes = new byte[checked((int)input.Length)];
        input.ReadExactly(bytes);
        if (input.ReadByte() != -1) throw new InvalidDataException("Source file changed during read");
        return bytes;
    }

    private sealed class ExactLoader : IFileLoader, IDisposable
    {
        private readonly Dictionary<string, JsonElement> files = new(StringComparer.Ordinal);
        private readonly Dictionary<string, string[]> shaderFiles = new(StringComparer.Ordinal);
        private readonly Dictionary<string, ShaderCollection> shaders = new(StringComparer.Ordinal);
        private readonly List<Resource> resources = [];
        private readonly HashSet<string> textureBudgets = [];
        private long decodedBytes;
        internal readonly HashSet<string> LoadedResources = [];
        internal readonly HashSet<string> LoadedShaders = [];
        internal object[] Identities => files.OrderBy(x => x.Key).Select(x => (object)new {
            resource = x.Key, sha256 = x.Value.GetProperty("sha256").GetString(), bytes = x.Value.GetProperty("bytes").GetInt64() }).ToArray();

        internal ExactLoader(JsonElement request)
        {
            long total = 0;
            foreach (var row in request.GetProperty("files").EnumerateArray())
            {
                var name = row.GetProperty("resource").GetString()!;
                if (name.Length is < 1 or > 512 || name != name.ToLowerInvariant() || name.Contains('\\')
                    || name.Contains(':') || name.StartsWith('/') || name.Split('/').Any(x => x is "" or "." or "..")
                    || !(name.EndsWith(".vmat_c") || name.EndsWith(".vtex_c") || name.EndsWith(".vcs"))
                    || !files.TryAdd(name, row)) throw new InvalidDataException("Invalid/duplicate explicit resource identity");
                var size = row.GetProperty("bytes").GetInt64();
                if (size is < 1 or > 32 * 1024 * 1024 || (total += size) > MaxBytes || files.Count > 512)
                    throw new InvalidDataException("Explicit source set exceeds bounds");
            }
            foreach (var row in request.GetProperty("shaders").EnumerateArray())
            {
                var name = row.GetProperty("name").GetString()!;
                var paths = row.GetProperty("files").EnumerateArray().Select(x => x.GetString()!).ToArray();
                if (!name.EndsWith(".vfx") || paths.Length is < 2 or > 12 || paths.Any(x => !x.EndsWith(".vcs") || !files.ContainsKey(x))
                    || !shaderFiles.TryAdd(name, paths) || shaderFiles.Count > 16)
                    throw new InvalidDataException("Explicit compiled shader collection required");
            }
        }
        internal string SourceSha(string name) => files[name].GetProperty("sha256").GetString()!;
        private byte[] Read(string name)
        {
            if (!files.TryGetValue(name, out var row)) throw new FileNotFoundException("Undeclared resource: " + name);
            var bytes = ReadBounded(row.GetProperty("path").GetString()!, 32 * 1024 * 1024);
            if (bytes.Length != row.GetProperty("bytes").GetInt64() || Digest(bytes) != SourceSha(name))
                throw new InvalidDataException("Source identity changed: " + name);
            return bytes;
        }
        public Resource? LoadFile(string file)
        {
            var bytes = Read(file);
            var type = file.EndsWith(".vmat_c") ? ResourceType.Material : file.EndsWith(".vtex_c")
                ? ResourceType.Texture : throw new InvalidDataException("Unexpected resource type");
            var resource = new Resource(type) { FileName = file };
            resource.Read(new MemoryStream(bytes, writable: false));
            resources.Add(resource); LoadedResources.Add(file);
            if (resource.DataBlock is VTexture texture && textureBudgets.Add(file))
            {
                if (texture.Width is < 1 or > 4096 || texture.Height is < 1 or > 4096 || texture.Depth != 1
                    || ((int)texture.Flags & (16 | 32 | 64)) != 0
                    || (decodedBytes += (long)texture.Width * texture.Height * 16) > MaxBytes)
                    throw new InvalidDataException("Material bundle requires bounded2D textures");
            }
            return resource;
        }
        public Resource? LoadFileCompiled(string file) => LoadFile(file.EndsWith("_c") ? file : file + "_c");
        public ShaderCollection? LoadShader(string shaderName)
        {
            if (shaders.TryGetValue(shaderName, out var existing)) return existing;
            if (!shaderFiles.TryGetValue(shaderName, out var paths)) throw new FileNotFoundException("Undeclared compiled shader: " + shaderName);
            var collection = new ShaderCollection();
            shaders.Add(shaderName, collection);
            foreach (var path in paths)
            {
                var shader = new VfxProgramData();
                shader.Read(Path.GetFileName(path), new MemoryStream(Read(path), writable: false));
                if (shader.ShaderName != Path.GetFileNameWithoutExtension(shaderName))
                    throw new InvalidDataException("Compiled shader name mismatch");
                collection.Add(shader);
            }
            if (collection.Features == null || collection.Pixel == null)
                throw new InvalidDataException("Compiled shader feature/pixel pair required");
            LoadedShaders.Add(shaderName);
            return collection;
        }
        public Stream? GetFileStream(string file) => throw new InvalidDataException("Raw file lookup is not part of material-bundle-v1");
        public void Dispose()
        {
            foreach (var resource in resources) resource.Dispose();
            foreach (var collection in shaders.Values) collection.Dispose();
        }
    }
}
