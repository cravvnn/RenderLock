// Offline bounded cubemap array extraction with VRF bitmap oracle.
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using TinyBCSharp;
using ValveResourceFormat;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.TextureDecoders;

if(args.Length!=2)throw new ArgumentException("config.json new-output-directory");
if(Directory.Exists(args[1]))throw new IOException("Output exists");
using var config=JsonDocument.Parse(File.ReadAllText(args[0]));var c=config.RootElement;
var path=c.GetProperty("path").GetString()!;var hash=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
if(hash!=c.GetProperty("sha256").GetString())throw new InvalidDataException("Texture identity changed");
var indices=c.GetProperty("indices").EnumerateArray().Select(v=>v.GetInt32()).ToArray();
if(indices.Length is <1 or >4||indices.Distinct().Count()!=indices.Length||indices.Any(i=>i<0))throw new InvalidDataException("Invalid bounded array selection");
using var resource=new Resource();resource.Read(path);var texture=(Texture)resource.DataBlock!;
if(texture.Width!=256||texture.Height!=256||texture.Depth is not (168 or 191 or 232)||texture.NumMipLevels!=7||(int)texture.Format!=19||(int)texture.Flags!=80)
    throw new InvalidDataException("Unqualified cube array layout");
if(indices.Any(i=>i>=texture.Depth))throw new InvalidDataException("Invalid bounded array selection");
if(texture.RadianceCoefficients?.Length!=texture.Depth*27)throw new InvalidDataException("Missing radiance coefficients");
Directory.CreateDirectory(args[1]);var rows=new List<object>();var buffer=new byte[texture.GetBiggestBufferSize()];var decoder=BlockDecoder.Create(BlockFormat.BC6HUf32);
// ReadTexture/GenerateBitmap share the resource reader, so finish each mip read
// before invoking the oracle and reload iteration offsets via a separate reader.
using var oracleResource=new Resource();oracleResource.Read(path);var oracleTexture=(Texture)oracleResource.DataBlock!;
foreach(var mip in texture.GetEveryMipLevelTexture(buffer))
{
    var faceSize=BlockDecoder.SourceSize(mip.Width,mip.Height,BlockFormat.BC6HUf32);
    if(faceSize*6*texture.Depth!=mip.BufferSize)throw new InvalidDataException("Cube face stride mismatch");
    foreach(var index in indices)
    for(var face=0;face<6;face++)
    {
        var bytes=new byte[mip.Width*mip.Height*16];var offset=(index*6+face)*faceSize;
        decoder.Decode(buffer.AsSpan(offset,faceSize),mip.Width,mip.Height,bytes,mip.Width,mip.Height);
        using var bitmap=oracleTexture.GenerateBitmap((uint)index,(Texture.CubemapFace)face,mip.Level,TextureCodec.None);
        using var pixmap=bitmap.PeekPixels();var actual=pixmap.GetPixelSpan<byte>();
        if(!actual.SequenceEqual(bytes))throw new InvalidDataException($"Bitmap mismatch {index}/{face}/{mip.Level}");
        foreach(var value in MemoryMarshal.Cast<byte,float>(bytes))if(!float.IsFinite(value)||value<0)throw new InvalidDataException("Invalid decoded pixel");
        var name=$"cube-{index}-face-{face}-mip-{mip.Level}.rgba32f";File.WriteAllBytes(Path.Combine(args[1],name),bytes);
        rows.Add(new{name,index,face,mip=mip.Level,width=mip.Width,height=mip.Height,components=bytes.Length/4,sha256=Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant()});
    }
}
var coefficients=indices.Select(i=>new{index=i,coefficients=texture.RadianceCoefficients.Skip(i*27).Take(27).ToArray()}).ToArray();
File.WriteAllText(Path.Combine(args[1],"decode-report.json"),JsonSerializer.Serialize(new{status="PASS",source_sha256=hash,layout="RGBA32F little endian unflipped rows; source cube enum faces",rows,coefficients},new JsonSerializerOptions{WriteIndented=true}));
Console.WriteLine($"Decoded {rows.Count} selected faces/mips, all components match VRF bitmap");
