using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

/// <summary>Bounded metadata only. No mesh/animation export or runtime lookup.</summary>
static class ParticleOwnerModelInventory
{
    public static void Run(string gameinfo,string name,string destination)
    {
        if(string.IsNullOrEmpty(name)||!name.EndsWith(".vmdl",StringComparison.Ordinal)
            ||name.Contains("..")||name.Contains(':')||name.Contains('\\')||name.StartsWith('/'))
            throw new InvalidDataException("Expected an exact relative particle-owner model");
        if(File.Exists(destination)||Directory.Exists(destination))
            throw new InvalidDataException("Choose a fresh owner-model metadata output");
        using var loader=new GameFileLoader(null,gameinfo,NullLogger.Instance);
        using var resource=loader.LoadFileCompiled(name)??throw new FileNotFoundException(name);
        var model=resource.DataBlock as Model??throw new InvalidDataException("Expected a model resource");
        var stream=resource.Reader!.BaseStream;
        if(!stream.CanSeek||stream.Length is <=0 or >256*1024*1024)
            throw new InvalidDataException("Owner model source exceeds inventory bounds");
        stream.Position=0;var digest=Convert.ToHexStringLower(SHA256.HashData(stream));
        var text=model.Data.GetSubCollection("m_modelInfo").GetStringProperty("m_keyValueText");
        if(text?.Length>1024*1024)throw new InvalidDataException("Owner model key-value metadata exceeds bounds");
        const string type="CitadelModelParticleSettings_t";
        var present=model.KeyValues.ContainsKey(type);
        var settings=present?model.KeyValues.GetSubCollection(type).ToKV3String():null;
        if(settings?.Length>65536)throw new InvalidDataException("Particle model settings exceed bounds");
        using var output=new FileStream(destination,FileMode.CreateNew,FileAccess.Write,FileShare.None);
        JsonSerializer.Serialize(output,new{schema_version=1,policy="agr_particle_owner_model_metadata_v1",
            resource=name,source_sha256=digest,settings_class=type,settings_present=present,settings_kv3=settings,
            scope="Typed installed model key-value metadata only. Missing settings stay absent; native fallback, runtime model/attachment overrides and effect activation require separate qualification."},
            new JsonSerializerOptions{WriteIndented=true});
        output.Flush(true);
        Console.WriteLine("Inventoried particle-owner model metadata; no geometry output");
    }
}
