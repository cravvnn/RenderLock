using ValveKeyValue;
using ValveResourceFormat.Serialization.KeyValues;

// Fail closed on authored geometry/material routes this adapter cannot export.
// These checks establish supported layout, not complete Source lighting parity.
static class WorldNodeLayout
{
    internal static readonly string[] UnsupportedArrays = [
        "m_clutterSceneObjects", "m_rtProxies", "m_extraVertexStreamOverrides",
        "m_materialOverrides", "m_extraVertexStreams", "m_aggregateInstanceStreams",
        "m_vertexAlbedoStreams", "m_vertexEmissiveStreams"];
    internal static readonly string[] AggregateStreams = [
        "m_instanceStream", "m_vertexAlbedoStream", "m_vertexEmissiveStream"];

    public static void Validate(KVObject node, string resource)
    {
        void Reject(string field) => throw new InvalidDataException(
            $"Unqualified world-node layout: {resource}: {field}");
        foreach (var field in UnsupportedArrays)
            if (!node.ContainsKey(field) || node.GetArray(field).Count != 0) Reject(field);
        if (!node.ContainsKey("m_grassFileName") || node.GetStringProperty("m_grassFileName") != "")
            Reject("m_grassFileName");
        int index = 0;
        foreach (var obj in node.GetArray("m_sceneObjects"))
        {
            foreach (var field in new[] { "m_skin", "m_nLODOverride", "m_renderable" })
                if (!obj.ContainsKey(field)) Reject($"scene/{index}/{field} missing");
            if (obj.GetStringProperty("m_skin") is not ("" or "default")) Reject($"scene/{index}/m_skin");
            if (obj.GetInt32Property("m_nLODOverride") != -1) Reject($"scene/{index}/m_nLODOverride");
            if (obj.GetStringProperty("m_renderable") != "") Reject($"scene/{index}/m_renderable");
            index++;
        }
        index = 0;
        foreach (var obj in node.GetArray("m_aggregateSceneObjects"))
        {
            foreach (var field in AggregateStreams)
                if (!obj.ContainsKey(field) || obj.GetInt32Property(field) != -1)
                    Reject($"aggregate/{index}/{field}");
            index++;
        }
    }

    public static void SelfTest()
    {
        KVObject Fixture()
        {
            var node = KVObject.Collection();
            foreach (var field in UnsupportedArrays) node[field] = KVObject.Array([]);
            node["m_grassFileName"] = "";
            var obj = KVObject.Collection();
            obj["m_skin"] = "default"; obj["m_nLODOverride"] = -1; obj["m_renderable"] = "";
            node["m_sceneObjects"] = KVObject.Array([obj]);
            var aggregate = KVObject.Collection();
            foreach (var field in AggregateStreams) aggregate[field] = -1;
            node["m_aggregateSceneObjects"] = KVObject.Array([aggregate]);
            return node;
        }
        var passed = 0;
        void RejectMutation(Action<KVObject> mutate, string expected)
        {
            var node = Fixture(); mutate(node);
            try { Validate(node, "fixture"); }
            catch (InvalidDataException error) when (error.Message.Contains(expected)) { passed++; return; }
            throw new Exception("Mutation was not rejected: " + expected);
        }
        Validate(Fixture(), "fixture"); passed++;
        foreach (var field in UnsupportedArrays)
            RejectMutation(n => n[field] = KVObject.Array([KVObject.Collection()]), field);
        RejectMutation(n => n["m_grassFileName"] = "grass.vgrs", "m_grassFileName");
        RejectMutation(n => n.GetArray("m_sceneObjects")[0]["m_skin"] = "alternate", "m_skin");
        RejectMutation(n => n.GetArray("m_sceneObjects")[0]["m_nLODOverride"] = 0, "m_nLODOverride");
        RejectMutation(n => n.GetArray("m_sceneObjects")[0]["m_renderable"] = "other.vmesh", "m_renderable");
        foreach (var field in AggregateStreams)
            RejectMutation(n => n.GetArray("m_aggregateSceneObjects")[0][field] = 0, field);
        Console.WriteLine($"World-node layout checks passed: {passed}");
    }
}
