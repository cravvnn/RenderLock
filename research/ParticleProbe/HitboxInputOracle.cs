#if AGR_SIMULATION_HITBOX
using System.Text.Json;
using System.Text.Json.Nodes;
using ValveResourceFormat.Particles;

static class HitboxInputOracle
{
    static JsonObject Instance(JsonObject root)=>root["instances"]!.AsArray()[0]!.AsObject();
    static JsonArray Points(JsonObject root)=>Instance(root)["control_points"]!.AsArray();
    static HitboxRuntimeInputs Parse(JsonObject root)
    {
        using var doc=JsonDocument.Parse(root.ToJsonString());
        var instance=doc.RootElement.GetProperty("instances")[0];
        var controls=instance.GetProperty("control_points").EnumerateArray().ToDictionary(p=>p.GetProperty("tick").GetInt32());
        return HitboxRuntimeInputs.Parse(instance,controls,instance.GetProperty("created_tick").GetInt32(),doc.RootElement.GetProperty("end_tick").GetInt32())!;
    }
    public static void Check(string input,string output)
    {
        var raw=File.ReadAllText(input);JsonObject Fresh()=>JsonNode.Parse(raw)!.AsObject();
        var negative=new List<string>();
        void Reject(Action action,string name)
        {
            try{action();}catch(InvalidDataException){negative.Add(name);return;}
            throw new Exception("Missing hitbox input guard: "+name);
        }
        var mutations=new (string Name,Action<JsonObject> Change)[]{
            ("unknown policy",r=>Instance(r)["model_hitbox_policy"]="unknown"),
            ("missing frame",r=>Points(r)[1]!.AsObject().Remove("model_hitboxes")),
            ("empty bindings",r=>Instance(r)["model_hitbox_bindings"]=new JsonArray()),
            ("model hash edit",r=>Instance(r)["model_hitbox_bindings"]![0]!["model_sha256"]=new string('b',64)),
            ("owner edit",r=>Instance(r)["model_hitbox_bindings"]![0]!["entity_id"]="58:284"),
            ("bone order edit",r=>Instance(r)["model_hitbox_bindings"]![0]!["bone_order_sha256"]=new string('b',64)),
            ("frame binding edit",r=>Points(r)[1]!["model_hitboxes"]![0]!["binding_id"]=new string('f',64)),
            ("missing CP",r=>Points(r)[1]!["model_hitboxes"]=new JsonArray()),
            ("duplicate CP",r=>Points(r)[0]!["model_hitboxes"]!.AsArray().Add(Points(r)[0]!["model_hitboxes"]![0]!.DeepClone())),
            ("authored bounds edit",r=>{var sets=Points(r)[1]!["model_hitboxes"]![0]!["sets"]!.AsObject();var record=sets.First().Value![0]!;record["min"]![0]=record["min"]![0]!.GetValue<float>()-1;}),
            ("zero quaternion",r=>{var sets=Points(r)[1]!["model_hitboxes"]![0]!["sets"]!.AsObject();var t=sets.First().Value![0]!["transform"]!;for(int i=4;i<8;i++)t[i]=0;}),
            ("missing set",r=>{var sets=Points(r)[1]!["model_hitboxes"]![0]!["sets"]!.AsObject();sets.Remove(sets.First().Key);})
        };
        foreach(var (name,change) in mutations){var r=Fresh();change(r);Reject(()=>Parse(r),name);}
        var request=Fresh();var source=Parse(request);var birth=Instance(request)["created_tick"]!.GetValue<int>();
        var a=new ParticleSystemState();var b=new ParticleSystemState();
        Reject(()=>source.Query(a,0,"core",false),"uninstalled frame");
        Reject(()=>source.Advance(birth+1),"skipped first frame");source.Advance(birth);
        var first=source.Query(a,0,"core",false);
        if(!first.Previous.IsEmpty)throw new Exception("Invented first previous frame");
        var other=source.Query(b,0,"core",false);
        if(ReferenceEquals(first,other)||!other.Previous.IsEmpty)throw new Exception("Consumer histories aliased");
        if(!ReferenceEquals(first,source.Query(a,0,"default",true)))throw new Exception("Same-frame consumer query advanced cache");
        Reject(()=>source.Query(a,1,"core",false),"CP1 alias");
        source.Advance(birth+1);a.Age=.015625f;var second=source.Query(a,0,"core",false);
        if(second.Previous.Length!=first.Current.Length||second.Current[0].Position.X-first.Current[0].Position.X!=5)
            throw new Exception("History did not follow installed frame");
        if(!ReferenceEquals(other,source.Query(b,0,"core",false)))throw new Exception("One consumer advanced another's clock");
        a.Age=0;Reject(()=>source.Query(a,0,"core",false),"consumer rewind");
        a.Age=.015625f;if(!ReferenceEquals(second,source.Query(a,0,"core",false)))throw new Exception("Rejected rewind changed history");
        Reject(()=>source.Advance(birth+1),"repeated frame installation");
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",rejected=negative.Count,negative,context_isolation=true,duplicate_reuse=true,request_frame_translation=5,rejected_request_preserves_history=true},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Hitbox request oracle PASS: {negative.Count} negative boundary checks and context/history checks");
    }
}

#endif
