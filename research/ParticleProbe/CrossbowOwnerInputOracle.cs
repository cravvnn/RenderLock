#if AGR_SIMULATION_SNAPSHOT
using System.Text.Json;

static class CrossbowOwnerInputOracle
{
    public static void Check(string path, string output)
    {
        using var doc = JsonDocument.Parse(File.ReadAllBytes(path));
        var rows = new List<object>();
        foreach (var row in doc.RootElement.EnumerateArray())
        {
            var instance = row.GetProperty("instance");
            bool accepted = false;
            string? error = null;
            try
            {
                var timeline = ReplayEventTimeline.Parse(instance, instance.GetProperty("created_tick").GetInt32(), row.GetProperty("end_tick").GetInt32());
                accepted = timeline.CrossbowOwnerProvider.HasValue;
            }
            catch (Exception e) { error = e.Message; }
            if (accepted != row.GetProperty("expected").GetBoolean())
                throw new InvalidDataException("Unexpected current Crossbow admission: " + row.GetProperty("name").GetString() + " / " + error);
            rows.Add(new { name = row.GetProperty("name").GetString(), accepted, error });
        }
        File.WriteAllText(output, JsonSerializer.Serialize(new { status = "PASS", cases = rows }));
        Console.WriteLine($"Current Crossbow owner/timeline {rows.Count} cases PASS");
    }
}
#endif
