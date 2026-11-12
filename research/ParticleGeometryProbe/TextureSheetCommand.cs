using System.Security.Cryptography;
using System.Text.Json;
using SkiaSharp;
using ValveResourceFormat;
using ValveResourceFormat.ResourceTypes;

// Preserve the original atlas and its UVs. Cropped MKS frames alone cannot
// reproduce animation/filtering across the original atlas's frame boundaries.
// Repeated equivalent image records are one rectangle, not split channels.
static class TextureSheetCommand
{
    public static void Run(string input,string output)
    {
        if(!Path.IsPathFullyQualified(input)||!Path.IsPathFullyQualified(output))
            throw new InvalidDataException("Absolute particle sheet paths required");
        if(!Directory.Exists(output)||Directory.EnumerateFileSystemEntries(output).Any())
            throw new IOException("Particle sheet destination must be an empty directory");
        var length=new FileInfo(input).Length;
        if(length is <=0 or >256*1024*1024)throw new InvalidDataException("Particle sheet input exceeds bound");
        var bytes=File.ReadAllBytes(input);
        if(bytes.LongLength!=length)throw new InvalidDataException("Particle sheet input changed while reading");
        // Hash and lazily decode the same bounded immutable snapshot. Opening
        // the path again would permit source replacement between these steps.
        using var snapshot=new MemoryStream(bytes,writable:false);
        using var resource=new Resource{FileName=input};resource.Read(snapshot);
        if(resource.DataBlock is not Texture texture||texture.Depth!=1
            ||(texture.Flags&(VTexFlags.CUBE_TEXTURE|VTexFlags.VOLUME_TEXTURE|VTexFlags.TEXTURE_ARRAY|VTexFlags.VIRTUAL_TEXTURE))!=0
            ||(long)texture.Width*texture.Height>64*1024*1024)
            throw new InvalidDataException("Particle sheet requires a flat texture");
        var sheet=texture.GetSpriteSheetData()??throw new InvalidDataException("No source particle sheet");
        if(sheet.Sequences.Length is <1 or >256)throw new InvalidDataException("Particle sheet sequence bound");
        if(sheet.Sequences.Sum(s=>(long)s.Frames.Length)>65536
            ||sheet.Sequences.Any(s=>s.AlphaCrop||s.NoColor||s.NoAlpha||s.Frames.Length is <1 or >4096
            ||s.Frames.Any(f=>!ParticleSheetImages.IsSingleImage(f))))
            throw new InvalidDataException("Cropped or split particle sheet images require separate qualification");
        using var bitmap=texture.GenerateBitmap();
        if(bitmap.Width<1||bitmap.Height<1||(long)bitmap.Width*bitmap.Height>64*1024*1024)
            throw new InvalidDataException("Particle atlas pixel bound");
        using var encoded=bitmap.Encode(SKEncodedImageFormat.Png,100);
        var png=encoded.ToArray();
        if(png.Length>256*1024*1024)throw new InvalidDataException("Particle atlas output exceeds bound");
        using(var image=new FileStream(Path.Combine(output,"atlas.png"),FileMode.CreateNew,FileAccess.Write,FileShare.None))
        {
            image.Write(png);image.Flush(true);
        }
        float[] V(System.Numerics.Vector2 v)=>new[]{v.X,v.Y};
        var result=new{schema_version=1,policy="agr_source_particle_sheet_v1",
            input_sha256=Convert.ToHexStringLower(SHA256.HashData(bytes)),
            image="atlas.png",image_sha256=Convert.ToHexStringLower(SHA256.HashData(png)),
            width=bitmap.Width,height=bitmap.Height,
            motion_vectors=new{policy="agr_source_motion_vector_metadata_v1",metadata_present=texture.ExtraData.ContainsKey(VTexExtraData.METADATA),
                max_distance=texture.MotionVectorsMaxDistance,source_width=texture.Width,source_height=texture.Height,
                metadata_sha256=texture.ExtraData.TryGetValue(VTexExtraData.METADATA,out var metadata)?Convert.ToHexStringLower(SHA256.HashData(metadata)):null},
            sequences=sheet.Sequences.Select((s,index)=>new{index,source_id=s.Id,clamp=s.Clamp,total_time=s.EffectiveTotalTime,
                frames=s.Frames.Select(f=>new{duration=f.DisplayTime,min=V(f.Images[0].UncroppedMin),max=V(f.Images[0].UncroppedMax)})})};
        using var descriptor=new FileStream(Path.Combine(output,"sheet.json"),FileMode.CreateNew,FileAccess.Write,FileShare.None);
        JsonSerializer.Serialize(descriptor,result);descriptor.Flush(true);
    }
}
