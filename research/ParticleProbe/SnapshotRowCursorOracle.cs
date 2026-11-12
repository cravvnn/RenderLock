#if AGR_SIMULATION_SNAPSHOT
using System.Text.Json;
using System.Security.Cryptography;
using ValveResourceFormat.Particles;

static class SnapshotRowCursorOracle
{
    public static void Check(string input,string output)
    {
        using var doc=JsonDocument.Parse(File.ReadAllBytes(input));
        int rows=0,resets=0,refusals=0;
        foreach(var trace in doc.RootElement.GetProperty("traces").EnumerateArray())
        {
            int start=trace.GetProperty("initial").GetInt32();
            var cursors=new[]{new SnapshotRowCursor(start),new SnapshotRowCursor(start)};
            foreach(var step in trace.GetProperty("steps").EnumerateArray())
            {
                int index=step.GetProperty("context").GetInt32();var cursor=cursors[index];
                int other=cursors[1-index].Position;
                if(cursor.Position!=step.GetProperty("before").GetInt32())throw new Exception("Native cursor load mismatch");
                foreach(var expected in step.GetProperty("selected").EnumerateArray())
                {
                    if(cursor.Take(step.GetProperty("count").GetInt32())!=expected.GetInt32())throw new Exception("Native snapshot row mismatch");
                    rows++;
                }
                if(cursor.Position!=step.GetProperty("after").GetInt32()||cursors[1-index].Position!=other)
                    throw new Exception("Native context store/isolation mismatch");
            }
            int reset=trace.GetProperty("reset_context").GetInt32();int saved=cursors[1-reset].Position;
            cursors[reset].Reset();
            if(cursors[reset].Position!=trace.GetProperty("after_reset").GetInt32()||cursors[1-reset].Position!=saved)
                throw new Exception("Native context initialization mismatch");
            resets++;
            foreach(int count in new[]{0,-1,int.MinValue})
            {
                int savedPosition=cursors[0].Position;
                try{cursors[0].Take(count);throw new Exception("Invalid snapshot count accepted");}
                catch(ArgumentOutOfRangeException){refusals++;}
                if(cursors[0].Position!=savedPosition)throw new Exception("Rejected count changed context");
            }
        }
        try{_ = new SnapshotRowCursor(-1);throw new Exception("Negative snapshot start accepted");}
        catch(ArgumentOutOfRangeException){refusals++;}
        var boundary=new SnapshotRowCursor(int.MaxValue-1);
        if(boundary.Take(int.MaxValue)!=int.MaxValue-1||boundary.Position!=int.MaxValue
            ||boundary.Take(int.MaxValue)!=0||boundary.Position!=1)throw new Exception("Snapshot cursor integer boundary mismatch");
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",native_rows=rows,native_resets=resets,
            invalid_input_refusals=refusals,integer_boundary_pass=true,
            input_sha256=Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(input))),
            scope="Actual C# row component matches native prepared context load/default selection/store/init traces. No full initializer or restart scheduling qualification."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Snapshot row context PASS: {rows} native rows");
    }
}
#endif
