using System.Globalization;
using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

// Inventory only. Geometry export, material conversion, particle selectors and
// scene admission remain separate consumers with their own identity checks.
static class ParticleModelMaterialsInventory
{
    public static void Run(string gameinfo, string name)
    {
        if (string.IsNullOrEmpty(name) || !name.EndsWith(".vmdl", StringComparison.Ordinal)
            || name.Contains("..") || name.Contains(':') || name.Contains('\\') || name.StartsWith('/'))
            throw new InvalidDataException("Expected a relative particle model resource");
        using var loader = new GameFileLoader(null, gameinfo, NullLogger.Instance);
        using var resource = loader.LoadFileCompiled(name) ?? throw new FileNotFoundException(name);
        var model = resource.DataBlock as Model ?? throw new InvalidDataException("Expected a model");
        var stream = resource.Reader!.BaseStream;
        if (stream.Length > 64L * 1024 * 1024) throw new InvalidDataException("Model inventory source exceeds64MiB");
        stream.Position = 0;
        var sourceHash = Convert.ToHexStringLower(SHA256.HashData(stream));
        var meshes = new List<object>();
        var indices = new HashSet<int>();
        var lod = model.LodInfo.LowestLevel;
        void Add(Mesh mesh, int index, string sourceName, bool embedded)
        {
            if (meshes.Count >= 256 || index < 0 || !indices.Add(index) || string.IsNullOrEmpty(sourceName))
                throw new InvalidDataException("Distinct bounded source mesh identities required");
            var calls = mesh.Data.GetArray("m_sceneObjects")
                .SelectMany(row => row.GetArray("m_drawCalls")).ToArray();
            if (calls.Length is < 1 or > 4096) throw new InvalidDataException("Bounded nonempty draw calls required");
            var primitives = calls.Select((call, ordinal) =>
            {
                var material = Mesh.GetMaterialName(call);
                if (string.IsNullOrEmpty(material) || !material.EndsWith(".vmat", StringComparison.Ordinal)
                    || material.Contains("..") || material.Contains(':') || material.Contains('\\') || material.StartsWith('/'))
                    throw new InvalidDataException("Exact authored draw-call VMAT resource required");
                var vertices = call.GetInt32Property("m_nVertexCount");
                var count = call.GetInt32Property("m_nIndexCount");
                if (vertices is < 1 or > 1000000 || count is < 3 or > 3000000 || count % 3 != 0)
                    throw new InvalidDataException("Invalid bounded draw-call geometry cardinality");
                var bufferInfo = call.GetSubCollection("m_indexBuffer");
                var bufferIndex = bufferInfo.GetInt32Property("m_hBuffer");
                if (bufferIndex < 0 || bufferIndex >= mesh.VBIB.IndexBuffers.Count)
                    throw new InvalidDataException("Invalid source index buffer identity");
                var buffer = mesh.VBIB.IndexBuffers[bufferIndex];
                var start = call.GetInt32Property("m_nStartIndex");
                var baseVertex = call.GetInt32Property("m_nBaseVertex");
                if (start < 0 || buffer.ElementSizeInBytes is not (2 or 4)
                    || ((long)start + count) * buffer.ElementSizeInBytes > buffer.Data.LongLength)
                    throw new InvalidDataException("Source draw-call index range exceeds buffer");
                var sourceIndices = GltfModelExporter.ReadIndices(buffer, start, count, baseVertex);
                var canonicalIndices = new byte[count * sizeof(int)];
                for (var i = 0; i < count; i++)
                {
                    if (sourceIndices[i] < 0) throw new InvalidDataException("Negative exported source vertex index");
                    BinaryPrimitives.WriteInt32LittleEndian(canonicalIndices.AsSpan(i * sizeof(int), sizeof(int)), sourceIndices[i]);
                }
                var indexHash = Convert.ToHexStringLower(SHA256.HashData(canonicalIndices));
                var tint = call.ContainsKey("m_vTintColor") ? call.GetSubCollection("m_vTintColor").ToVector3() : System.Numerics.Vector3.One;
                var alpha = call.GetFloatProperty("m_flAlpha", 1f);
                if (!float.IsFinite(tint.X) || !float.IsFinite(tint.Y) || !float.IsFinite(tint.Z) || !float.IsFinite(alpha))
                    throw new InvalidDataException("Nonfinite authored draw-call tint");
                return new { primitive = ordinal, material, vertices, indices = count,
                    index_buffer = bufferIndex, start_index = start, base_vertex = baseVertex,
                    indices_sha256 = indexHash, tint = new[] { tint.X, tint.Y, tint.Z, alpha } };
            }).ToArray();
            meshes.Add(new { source_mesh_index = index, source_name = sourceName, embedded,
                mesh_group_mask = model.MeshGroups.GetMeshMask(index).ToString(CultureInfo.InvariantCulture), primitives });
        }
        foreach (var mesh in model.GetEmbeddedMeshesForLod(lod)) Add(mesh.Mesh, mesh.MeshIndex, mesh.Name, true);
        foreach (var mesh in model.GetReferenceMeshNamesForLod(lod))
        {
            using var referenced = loader.LoadFileCompiled(mesh.MeshName) ?? throw new FileNotFoundException(mesh.MeshName);
            Add(referenced.DataBlock as Mesh ?? throw new InvalidDataException("Expected a referenced mesh"),
                mesh.MeshIndex, Path.GetFileNameWithoutExtension(mesh.MeshName), false);
        }
        if (meshes.Count == 0) throw new InvalidDataException("No model meshes in exporter LOD");
        Console.WriteLine(JsonSerializer.Serialize(new { schema_version = 1,
            policy = "agr_particle_authored_mesh_material_inventory_v1", resource = name, source_sha256 = sourceHash,
            exporter_lod = lod, mesh_groups = model.MeshGroups.Names,
            body_groups = model.MeshGroups.BodyGroups.Select(group => new { name = group.Name,
                choices = group.Choices.Select(choice => new { group_index = choice.GroupIndex, name = choice.Name, full_name = choice.FullName }) }),
            meshes, scope = "Ordered draw calls and original VMAT identities; no shader conversion, particle placement, selector or rendering admission" }));
    }
}
