// Bounded offline atlas extraction; compare crops with VRF GenerateBitmap.
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using TinyBCSharp;
using ValveResourceFormat;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.TextureDecoders;

if (args.Length != 2) throw new ArgumentException("config.json new-output-directory");
if (Directory.Exists(args[1])) throw new IOException("Output exists");
using var config = JsonDocument.Parse(File.ReadAllText(args[0]));
var outputs = new List<object>();
Directory.CreateDirectory(args[1]);
foreach (var item in config.RootElement.EnumerateArray())
{
    var path = item.GetProperty("path").GetString()!;
    var hash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    if (hash != item.GetProperty("sha256").GetString()) throw new InvalidDataException("Source hash mismatch");
    using var resource = new Resource(); resource.Read(path);
    var texture = (Texture)resource.DataBlock!;
    var hdr = (int)texture.Format == 19;
    var legacyAtlas = texture.Width == 240 && texture.Height is (224 or 236) && texture.Depth == (hdr ? 1536 : 256);
    var currentAtlas = texture.Width == 244 && texture.Height == 236 && texture.Depth == (hdr ? 1416 : 236);
    if ((!hdr && (int)texture.Format != 20) || (int)texture.Flags != 32 || texture.NumMipLevels != 1
        || (!legacyAtlas && !currentAtlas))
        throw new InvalidDataException("Unqualified atlas layout");
    var buffer = new byte[texture.GetBiggestBufferSize()];
    var mip = texture.GetEveryMipLevelTexture(buffer).Single();
    var format = hdr ? BlockFormat.BC6HUf32 : BlockFormat.BC7;
    var decoder = BlockDecoder.Create(format);
    var sliceSize = BlockDecoder.SourceSize(mip.Width, mip.Height, format);
    if (sliceSize * mip.Depth != mip.BufferSize) throw new InvalidDataException("Slice layout mismatch");
    var pixels = new byte[mip.Width * mip.Height * (hdr ? 16 : 4)];
    foreach (var box in item.GetProperty("regions").EnumerateArray())
    {
        var name = box.GetProperty("name").GetString()!;
        if (name.Length == 0 || name.Any(c => !char.IsAsciiLetterOrDigit(c) && c != '-' && c != '_'))
            throw new InvalidDataException("Invalid output name");
        var start = box.GetProperty("start").EnumerateArray().Select(v => v.GetInt32()).ToArray();
        var size = box.GetProperty("size").EnumerateArray().Select(v => v.GetInt32()).ToArray();
        if (start.Length != 3 || size.Length != 3 || size.Any(x => x <= 0) || start.Any(x => x < 0)
            || (long)size[0]*size[1]*size[2]*16 > 128*1024*1024
            || (long)start[0]+size[0] > mip.Width || (long)start[1]+size[1] > mip.Height || (long)start[2]+size[2] > mip.Depth)
            throw new InvalidDataException("Invalid bounded region");
        var filename = Path.Combine(args[1], name + ".rgba32f");
        using var file = new FileStream(filename, FileMode.CreateNew);
        using var writer = new BinaryWriter(file);
        var oracleComponents = 0;
        for (var z = start[2]; z < start[2] + size[2]; z++)
        {
            decoder.Decode(buffer.AsSpan(z*sliceSize, sliceSize), mip.Width, mip.Height, pixels, mip.Width, mip.Height);
            using var oracle = z == start[2] + size[2]/2 ? texture.GenerateBitmap((uint)z, decodeFlags: TextureCodec.None) : null;
            using var pixmap = oracle?.PeekPixels();
            for (var y = start[1]; y < start[1] + size[1]; y++)
            for (var x = start[0]; x < start[0] + size[0]; x++)
            for (var c = 0; c < 4; c++)
            {
                var index = (y*mip.Width+x)*4+c;
                var value = hdr ? MemoryMarshal.Cast<byte,float>(pixels)[index] : pixels[index]/255f;
                if (!float.IsFinite(value) || value < 0) throw new InvalidDataException("Invalid decoded value");
                if (pixmap != null)
                {
                    var bytes = pixmap.GetPixelSpan<byte>();
                    var expected = hdr ? MemoryMarshal.Cast<byte,float>(bytes)[index]
                        : bytes[(y*mip.Width+x)*4+(c == 0 ? 2 : c == 2 ? 0 : c)]/255f;
                    if (value != expected) throw new InvalidDataException($"Bitmap mismatch {name} {x},{y},{z},{c}");
                    oracleComponents++;
                }
                writer.Write(value);
            }
        }
        writer.Flush();
        outputs.Add(new { name, source_sha256=hash, start, size, format="little-endian float32 RGBA, z/y/x/component order, unflipped source rows",
                         bytes=file.Length, oracle_components=oracleComponents });
    }
}
File.WriteAllText(Path.Combine(args[1], "decode-report.json"), JsonSerializer.Serialize(outputs, new JsonSerializerOptions { WriteIndented=true }));
Console.WriteLine($"Decoded {outputs.Count} bounded regions; midpoint slices match VRF bitmap exactly");
