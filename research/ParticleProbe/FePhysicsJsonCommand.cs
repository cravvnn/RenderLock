using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using ValveKeyValue;
using ValveResourceFormat;
using ValveResourceFormat.ResourceTypes;

/// <summary>Binary PHYS transport without the display serializer's six-digit rounding.</summary>
static class FePhysicsJsonCommand
{
    const long MaximumOutput = 32 * 1024 * 1024;

    public static void Run(string source, string expectedSha256, string destination)
    {
        if (!Path.IsPathFullyQualified(source) || !Path.IsPathFullyQualified(destination)
            || expectedSha256.Length != 64 || !expectedSha256.All(Uri.IsHexDigit)
            || new FileInfo(source).Length is <= 0 or > 256 * 1024 * 1024)
            throw new InvalidDataException("Bounded absolute compiled resource and SHA256 required");
        using var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read);
        var digest = Convert.ToHexStringLower(SHA256.HashData(input));
        if (!string.Equals(digest, expectedSha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Compiled PHYS source SHA256 changed");
        input.Position = 0;
        using var resource = new Resource();
        resource.Read(input, leaveOpen: true);
        string[] references = [];
        PhysAggregateData? physics;
        string status;
        if (resource.DataBlock is Model model)
        {
            references = model.GetReferencedPhysNames()?.ToArray() ?? [];
            if (references.Length > 1 || references.Any(name => string.IsNullOrEmpty(name)
                || !name.EndsWith(".vphys", StringComparison.Ordinal) || name.Contains("..")
                || name.Contains(':') || name.StartsWith('/') || name.Contains('\\')))
                throw new InvalidDataException("Unqualified referenced PHYS resource inventory");
            physics = references.Length == 0 ? model.GetEmbeddedPhys() : null;
            status = references.Length > 0 ? "referenced" : physics is null ? "not_applicable" : "embedded";
        }
        else if (resource.DataBlock is PhysAggregateData direct)
        {
            physics = direct;
            status = "direct";
        }
        else throw new InvalidDataException("Expected a compiled model or physics aggregate");

        using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        using var writer = new Utf8JsonWriter(output);
        writer.WriteStartObject();
        writer.WriteNumber("schema_version", 1);
        writer.WriteString("resource_sha256", digest);
        writer.WriteString("numeric_encoding", "binary_kv3_roundtrip_v1");
        writer.WriteString("status", status);
        writer.WritePropertyName("referenced_physics");
        JsonSerializer.Serialize(writer, references);
        writer.WritePropertyName("physics");
        if (status == "referenced") writer.WriteNullValue();
        else
        {
            writer.WriteStartObject();
            writer.WritePropertyName("m_pFeModel");
            int remaining = 2_000_000;
            // Preserve the raw FE subtree, not FeModel's computed accessors.
            // A genuinely absent FE model is explicit; no shape is inferred.
            if (physics?.Data.TryGetValue("m_pFeModel", out var fe) == true)
                WriteValue(writer, fe, 0, ref remaining);
            else writer.WriteNullValue();
            writer.WriteEndObject();
        }
        writer.WriteEndObject();
        writer.Flush();
        if (output.Length > MaximumOutput) throw new InvalidDataException("PHYS JSON exceeds supported bound");
        output.Flush(true);
        Console.WriteLine("Binary PHYS numeric transport completed");
    }

    internal static void WriteValue(Utf8JsonWriter writer, KVObject value, int depth, ref int remaining)
    {
        if (depth > 64 || --remaining < 0 || writer.BytesCommitted + writer.BytesPending > MaximumOutput)
            throw new InvalidDataException("PHYS KV tree exceeds supported bounds");
        if (value.IsCollection)
        {
            if (value.Count > 100_000) throw new InvalidDataException("Excessive PHYS object fields");
            writer.WriteStartObject();
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var (name, child) in value.Children)
            {
                if (name is null || name.Length > 4096 || !names.Add(name))
                    throw new InvalidDataException("PHYS object field identity differs");
                writer.WritePropertyName(name);
                WriteValue(writer, child, depth + 1, ref remaining);
            }
            writer.WriteEndObject();
        }
        else if (value.IsArray)
        {
            if (value.Count > 1_000_000) throw new InvalidDataException("Excessive PHYS array entries");
            writer.WriteStartArray();
            foreach (var child in value.Values) WriteValue(writer, child, depth + 1, ref remaining);
            writer.WriteEndArray();
        }
        else switch (value.ValueType)
        {
            case KVValueType.Null: writer.WriteNullValue(); break;
            case KVValueType.Boolean: writer.WriteBooleanValue(value.ToBoolean(CultureInfo.InvariantCulture)); break;
            case KVValueType.String:
                var text = value.ToString(CultureInfo.InvariantCulture);
                if (text.Length > 1024 * 1024) throw new InvalidDataException("Excessive PHYS string");
                writer.WriteStringValue(text); break;
            case KVValueType.Int16: case KVValueType.Int32: case KVValueType.Int64:
                writer.WriteNumberValue(value.ToInt64(CultureInfo.InvariantCulture)); break;
            case KVValueType.UInt16: case KVValueType.UInt32: case KVValueType.UInt64:
                writer.WriteNumberValue(value.ToUInt64(CultureInfo.InvariantCulture)); break;
            case KVValueType.FloatingPoint:
                writer.WriteNumberValue(value.ToSingle(CultureInfo.InvariantCulture)); break;
            case KVValueType.FloatingPoint64:
                writer.WriteNumberValue(value.ToDouble(CultureInfo.InvariantCulture)); break;
            case KVValueType.BinaryBlob:
                var bytes = value.AsBlob();
                if (bytes.Length > 1_000_000) throw new InvalidDataException("Excessive PHYS byte payload");
                writer.WriteStartArray();
                foreach (var item in bytes) writer.WriteNumberValue(item);
                writer.WriteEndArray(); break;
            default: throw new InvalidDataException("Unqualified PHYS KV scalar: " + value.ValueType);
        }
    }
}
