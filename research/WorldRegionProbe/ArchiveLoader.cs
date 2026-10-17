using System.Collections.Concurrent;
using System.Security.Cryptography;
using SteamDatabase.ValvePak;
using ValveResourceFormat.IO;

// The caller supplies exactly the provider's ordered archives. No gameinfo,
// addon discovery or loose-file fallback may change resource precedence.
sealed class ArchiveLoader : GameFileLoader
{
    readonly List<(string Path, Package Package)> archives = [];
    public ConcurrentDictionary<string, PayloadIdentity> Payloads { get; } = new(StringComparer.Ordinal);
    public IEnumerable<string> Loaded => Payloads.Keys;
    public sealed record PayloadIdentity(string archive, string sha256);

    public ArchiveLoader(IEnumerable<string> paths) : base(null, null)
    {
        var names = paths.Select(Path.GetFullPath).ToArray();
        if (names.Length is < 1 or > 64 || names.Distinct(StringComparer.OrdinalIgnoreCase).Count() != names.Length)
            throw new InvalidDataException("Expected 1..64 distinct ordered VPK archives");
        try
        {
            foreach (var name in names)
            {
                var package = new Package();
                try { package.Read(name); }
                catch { package.Dispose(); throw; }
                archives.Add((name, package));
            }
        }
        catch { Dispose(); throw; }
    }

    public override (string? PathOnDisk, Package? Package, PackageEntry? PackageEntry) FindFile(string file, bool logNotFound = true)
    {
        file = file.Replace('\\', '/').ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(file) || file.Contains(':') ||
            file.StartsWith('/') || file.Split('/').Any(p => p is "" or "." or ".."))
            throw new InvalidDataException("Invalid archive resource path: " + file);
        foreach (var (path, package) in archives)
        {
            var entry = package.FindEntry(file);
            if (entry == null) continue;
            using var stream = GetPackageEntryStream(package, entry);
            var identity = new PayloadIdentity(path, Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant());
            var previous = Payloads.GetOrAdd(file, identity);
            if (previous != identity) throw new InvalidDataException("Installed resource changed during export: " + file);
            return (null, package, entry);
        }
        return (null, null, null);
    }

    public override ValveResourceFormat.Resource? LoadFile(string file)
    {
        var resource = base.LoadFile(file) ?? throw new FileNotFoundException("Missing required archive resource", file);
        // Confirm the source still matches after Resource.Read has parsed it.
        try { FindFile(file); return resource; }
        catch { resource.Dispose(); throw; }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            foreach (var (_, package) in archives) package.Dispose();
            archives.Clear();
        }
        base.Dispose(disposing);
    }
}
