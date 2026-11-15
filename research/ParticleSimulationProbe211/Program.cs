// Host for the Source 2 Viewer 21.1 particle simulation. The simulation lives in the "Tests" assembly because that
// is the name 21.1 grants internal access to; this host only gives it the file name RenderLock looks for.
using System.Reflection;
using System.Runtime.ExceptionServices;

var entry = Assembly.Load("Tests").EntryPoint ?? throw new InvalidOperationException("Simulation entry point missing");
try
{
    var result = entry.Invoke(null, [args]);
    if (result is Task task) { task.GetAwaiter().GetResult(); result = (task as Task<int>)?.Result; }
    return result is int code ? code : 0;
}
catch (TargetInvocationException error) when (error.InnerException is not null)
{
    ExceptionDispatchInfo.Capture(error.InnerException).Throw();
    throw;
}
