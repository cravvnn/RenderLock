#if AGR_SIMULATION_HITBOX
using System.Text.Json;

static class SilenceBirthInputOracle
{
    public static void Check(string path,string output)
    {
        using var doc=JsonDocument.Parse(File.ReadAllBytes(path));var rows=new List<object>();
        foreach(var row in doc.RootElement.EnumerateArray())
        {
            var instance=row.GetProperty("instance");bool accepted=false;string? error=null;
            try
            {
                var timeline=ReplayEventTimeline.Parse(instance,instance.GetProperty("created_tick").GetInt32(),row.GetProperty("end_tick").GetInt32());
                if(row.TryGetProperty("validate_controls",out var check)&&check.GetBoolean())
                {
                    var controls=instance.GetProperty("control_points").EnumerateArray().ToDictionary(p=>p.GetProperty("tick").GetInt32());
                    timeline.ValidateControls(controls,instance.GetProperty("created_tick").GetInt32(),row.GetProperty("end_tick").GetInt32());
                }
                accepted=timeline.SilenceBirthProvider.HasValue;
            }
            catch(Exception e){error=e.Message;}
            if(accepted!=row.GetProperty("expected").GetBoolean())throw new InvalidDataException("Unexpected Silence birth admission: "+row.GetProperty("name").GetString()+" / "+error);
            rows.Add(new{name=row.GetProperty("name").GetString(),accepted,error});
        }
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",cases=rows}));
        Console.WriteLine($"Silence birth admission/timeline {rows.Count} cases PASS");
    }
}

#endif
