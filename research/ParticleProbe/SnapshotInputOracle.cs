#if AGR_SIMULATION_SNAPSHOT
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

static class SnapshotInputOracle
{
    public static void Check(string output)
    {
        var sha=new string('a',64);var order=new string('b',64);
        var id=Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join('\n',"0","56:895","models/test.vmdl_c",sha,order,"crossbow"))));
        var header=new JsonObject { ["control_point"]=0,["entity_id"]="56:895",["model"]="models/test.vmdl_c",
            ["model_sha256"]=sha,["bone_order_sha256"]=order,["bone"]="crossbow",["binding_id"]=id };
        var root=new JsonObject { ["snapshot_model_policy"]=SnapshotRuntimeInputs.Policy,["snapshot_model_bindings"]=new JsonArray(header),["controls"]=new JsonArray() };
        for(int tick=10;tick<=12;tick++)
            root["controls"]!.AsArray().Add(new JsonObject { ["tick"]=tick,["snapshot_model_bones"]=new JsonArray(new JsonObject {
                ["control_point"]=0,["bone"]="crossbow",["binding_id"]=id,
                ["matrix"]=new JsonArray(1,0,0,tick,0,1,0,0,0,0,1,0) }) });
        SnapshotRuntimeInputs? Parse(JsonObject data)
        {
            using var doc=JsonDocument.Parse(data.ToJsonString());var instance=doc.RootElement;
            var controls=instance.GetProperty("controls").EnumerateArray().ToDictionary(r=>r.GetProperty("tick").GetInt32(),r=>r.Clone());
            return SnapshotRuntimeInputs.Parse(instance,controls,10,12);
        }
        int refusals=0,queries=0;
        void Refuse(Action action)
        {
            try{action();}
            catch(Exception e) when(e is InvalidDataException or InvalidOperationException or ArgumentException or KeyNotFoundException)
                {refusals++;return;}
            throw new Exception("Invalid snapshot transport accepted");
        }
        var input=Parse(root)!;
        Refuse(()=>input.Query(0,"crossbow"));Refuse(()=>input.Advance(11));Refuse(()=>input.Complete());
        for(int tick=10;tick<=12;tick++)
        {
            input.Advance(tick);var bone=input.Query(0,"crossbow");
            if(bone.Identity!=id||bone.Matrix.TransformPoint(new(1,2,3))!=new System.Numerics.Vector3(tick+1,2,3))
                throw new Exception("Wrong explicit snapshot frame/binding");queries++;
        }
        if(!input.Complete().SequenceEqual([id]))throw new Exception("Missing queried binding acknowledgement");
        Refuse(()=>input.Advance(12));Refuse(()=>input.Advance(13));Refuse(()=>input.Query(1,"crossbow"));Refuse(()=>input.Query(0,"other"));
        var unqueried=Parse(root)!;unqueried.Advance(10);unqueried.Advance(11);unqueried.Advance(12);Refuse(()=>unqueried.Complete());
        var other=Parse(root)!;Refuse(()=>other.Query(0,"crossbow"));other.Advance(10);other.Query(0,"crossbow");
        foreach(var name in new[]{"policy","bindings","frames","header_hash","model","bone_order","duplicate_header","missing_bone",
            "changed_frame_binding","changed_cp","changed_bone","duplicate_bone","short_matrix","long_matrix","matrix_object","long_model","scaled_matrix","reflected_matrix","extra_field","missing_contract"})
        {
            var bad=(JsonObject)root.DeepClone();var bindings=bad["snapshot_model_bindings"]!.AsArray();
            var controls=bad["controls"]!.AsArray();var frame=controls[1]!["snapshot_model_bones"]!.AsArray();
            var row=frame[0]!.AsObject();
            switch(name)
            {
                case "policy":bad["snapshot_model_policy"]="other";break;
                case "bindings":bad.Remove("snapshot_model_bindings");break;
                case "frames":controls.RemoveAt(1);break;
                case "header_hash":bindings[0]!["binding_id"]=new string('c',64);break;
                case "model":bindings[0]!["model"]="models/other.vmdl_c";break;
                case "bone_order":bindings[0]!["bone_order_sha256"]=new string('c',64);break;
                case "duplicate_header":bindings.Add(bindings[0]!.DeepClone());break;
                case "missing_bone":frame.Clear();break;
                case "changed_frame_binding":row["binding_id"]=new string('c',64);break;
                case "changed_cp":row["control_point"]=1;break;
                case "changed_bone":row["bone"]="other";break;
                case "duplicate_bone":frame.Add(row.DeepClone());break;
                case "short_matrix":row["matrix"]!.AsArray().RemoveAt(0);break;
                case "long_matrix":row["matrix"]!.AsArray().Add(0);break;
                case "matrix_object":row["matrix"]=new JsonObject();break;
                case "long_model":bindings[0]!["model"]="models/"+new string('a',512)+".vmdl_c";break;
                case "scaled_matrix":row["matrix"]![0]=2;break;
                case "reflected_matrix":row["matrix"]![0]=-1;break;
                case "extra_field":row["fallback"]=true;break;
                case "missing_contract":bad.Remove("snapshot_model_policy");break;
            }
            Refuse(()=>Parse(bad));
        }
        var absent=new JsonObject { ["controls"]=new JsonArray() };
        if(Parse(absent)!=null)throw new Exception("Legacy request unexpectedly enabled snapshot input");
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",frame_queries=queries,invalid_input_refusals=refusals,
            scope="Actual request parser/clock/query/completion: complete binding frames, isolated contexts, changed models/layouts, missing data and unused provider rejection. No game/acquisition or normal export."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Snapshot input transport PASS {queries}queries/{refusals}refusals");
    }
}
#endif
