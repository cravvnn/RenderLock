using System.Numerics;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveResourceFormat.IO;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

/// <summary>Exact compiled-model parent rest rows, without glTF or NM rest substitution.</summary>
static class ModelRestInventory
{
    public static void Run(string gameinfo,string name,string destination)
    {
        if(string.IsNullOrEmpty(name)||name.Length>512||!name.EndsWith(".vmdl",StringComparison.Ordinal)
            ||name.Contains(':')||name.Contains('\\')||name.Any(char.IsControl)
            ||name.Split('/').Any(part=>part is "" or "." or ".."))
            throw new InvalidDataException("Expected a relative compiled model resource name");
        if(File.Exists(destination)||Directory.Exists(destination))throw new InvalidDataException("Choose a fresh model rest output");
        using var loader=new GameFileLoader(null,gameinfo,NullLogger.Instance);
        using var resource=loader.LoadFileCompiled(name)??throw new FileNotFoundException(name);
        var model=resource.DataBlock as Model??throw new InvalidDataException("Expected a model");
        var stream=resource.Reader!.BaseStream;
        if(!stream.CanSeek||stream.Length is <=0 or >256*1024*1024)throw new InvalidDataException("Model rest source exceeds bounds");
        stream.Position=0;var digest=Convert.ToHexStringLower(SHA256.HashData(stream));
        var skeleton=model.Data.GetSubCollection("m_modelSkeleton");
        var names=skeleton.GetArray<string>("m_boneName");
        var parents=skeleton.GetIntegerArray("m_nParent");
        var positions=skeleton.GetArray("m_bonePosParent").Select(v=>v.ToVector3()).ToArray();
        var rotations=skeleton.GetArray("m_boneRotParent").Select(v=>v.ToQuaternion()).ToArray();
        var scales=skeleton.GetFloatArray("m_boneScaleParent");
        int count=names.Length;
        if(count is <1 or >4096||names.Any(string.IsNullOrEmpty)||names.Distinct(StringComparer.Ordinal).Count()!=count
            ||parents.Length!=count||positions.Length!=count||rotations.Length!=count||scales.Length!=count
            ||!names.SequenceEqual(model.Skeleton.Bones.Select(b=>b.Name)))
            throw new InvalidDataException("Model rest skeleton dimensions/order mismatch");
        var rows=new float[count][];
        for(int i=0;i<count;i++)
        {
            var chain=new HashSet<int>();int bone=i;
            while(bone>=0)
            {
                if(bone>=count||!chain.Add(bone)||parents[bone]<-1||parents[bone]>=count)
                    throw new InvalidDataException("Invalid model rest parent hierarchy");
                bone=checked((int)parents[bone]);
            }
            Vector3 p=positions[i];Quaternion q=rotations[i];float scale=scales[i];
            rows[i]=[p.X,p.Y,p.Z,scale,q.X,q.Y,q.Z,q.W];
            if(rows[i].Any(v=>!float.IsFinite(v))||scale<=0||MathF.Abs(q.LengthSquared()-1)>2e-4f)
                throw new InvalidDataException("Invalid authored model rest transform");
        }
        using var output=new FileStream(destination,FileMode.CreateNew,FileAccess.Write,FileShare.None);
        JsonSerializer.Serialize(output,new{schema_version=1,policy="agr_authored_model_rest_v1",model=name,
            source_sha256=digest,names,parents,parent_transforms=rows,
            scope="Exact typed compiled model parent rest rows in Source units/model order. Not imported glTF or NM skeleton rest; no runtime model provider selection."},
            new JsonSerializerOptions{WriteIndented=true});
        output.Flush(true);
        Console.WriteLine($"Inventoried {count} authored model rest rows; no geometry export");
    }
}
