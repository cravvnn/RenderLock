using DemoFile;
using System.Text.Json;

// Only decode recorded events/string tables. Entity schemas change with game
// updates; kill timestamps and recorded player names do not require them.
try
{
    if (args.Length != 1) throw new ArgumentException("Expected one completed .dem replay.");
    using var stream = File.OpenRead(args[0]);
    var demo = new DeadlockDemoParser();
    demo.DemoEvents.DemoClassInfo = null;
    demo.DemoEvents.DemoSendTables = null;
    demo.PacketEvents.SvcPacketEntities = null;
    var names = new Dictionary<int, string> { [0] = "World" };
    var kills = new List<Kill>();
    var totalTicks = 0;
    demo.DemoEvents.DemoFileInfo += e => totalTicks = e.PlaybackTicks;
    demo.Source1GameEvents.PlayerDeath += e =>
    {
        var victim = PlayerId(e.PlayerIndex.Value);
        var killer = PlayerId(e.AttackerIndex.Value);
        if (victim == 0 || e.Abort) return;
        if (kills.Count >= 3500) throw new InvalidDataException("Replay exceeds 3500 kill events.");
        Remember(names, killer, e.Attackername);
        Remember(names, victim, e.Victimname);
        kills.Add(new Kill(demo.CurrentDemoTick.Value, killer, victim));
    };
    var reader = DemoFileReader.Create(demo, stream);
    await reader.ReadAllAsync();
    if (totalTicks <= 0) throw new InvalidDataException("Replay is incomplete; its end tick is missing.");
    var sorted = kills.Distinct().OrderBy(e => e.tick).ToArray();
    if (sorted.Any(e => e.tick < 0 || e.tick >= totalTicks))
        throw new InvalidDataException("Kill event is outside the replay.");
    var participants = sorted.SelectMany(e => new[] { e.killer, e.victim }).Distinct().Order().ToArray();
    if (participants.Length > 32) throw new InvalidDataException("Replay exceeds 32 players.");
    var players = participants.Select(id => new Player(id,
        names.GetValueOrDefault(id, $"Player {id}"))).ToArray();
    Console.WriteLine(JsonSerializer.Serialize(new { abi = 1, total_ticks = totalTicks, kills = sorted, players }));
}
catch (Exception error)
{
    Console.Error.WriteLine(error.Message);
    Environment.ExitCode = 1;
}

static int PlayerId(uint value) => value <= 65535 ? (int)value : 0;
static void Remember(Dictionary<int, string> names, int id, string name)
{
    if (id != 0 && !string.IsNullOrWhiteSpace(name))
        names[id] = name.Length > 128 ? name[..128] : name;
}
record Kill(int tick, int killer, int victim);
record Player(int id, string name);
