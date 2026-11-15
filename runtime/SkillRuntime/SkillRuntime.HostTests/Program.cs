using System;
using System.Collections.Generic;
using System.Drawing;
using Reloaded.Mod.Interfaces.Internal;
using SkillRuntime.Reloaded;

internal sealed class Logger : ILoggerV1
{
    public readonly List<string> Messages = new();
    public Color BackgroundColor { get; set; }
    public Color TextColor { get; set; }
    public Color ColorRed { get; set; } = Color.Red;
    public Color ColorRedLight { get; set; } = Color.Red;
    public Color ColorGreen { get; set; } = Color.Green;
    public Color ColorGreenLight { get; set; } = Color.Green;
    public Color ColorYellow { get; set; } = Color.Yellow;
    public Color ColorYellowLight { get; set; } = Color.Yellow;
    public Color ColorBlue { get; set; } = Color.Blue;
    public Color ColorBlueLight { get; set; } = Color.Blue;
    public Color ColorPink { get; set; } = Color.Pink;
    public Color ColorPinkLight { get; set; } = Color.Pink;
    public Color ColorLightBlue { get; set; } = Color.LightBlue;
    public Color ColorLightBlueLight { get; set; } = Color.LightBlue;
#pragma warning disable CS0618
    public event EventHandler<string> OnPrintMessage { add { } remove { } }
    public void PrintMessage(string message, Color color) => Messages.Add(message);
#pragma warning restore CS0618
}
internal sealed class Loader : IModLoaderV1
{
    public readonly Logger Logger = new();
    public Action OnModLoaderInitialized { get; set; } = delegate { };
    public Action<IModV1, IModConfigV1> ModUnloading { get; set; } = delegate { };
    public Action<IModV1, IModConfigV1> ModLoading { get; set; } = delegate { };
    public Action<IModV1, IModConfigV1> ModLoaded { get; set; } = delegate { };
    public ILoggerV1 GetLogger() => Logger;
    private static Exception Unexpected() => new Exception("Dormant host requested a loader capability other than logging.");
    public Version GetLoaderVersion() => throw Unexpected();
    public IApplicationConfigV1 GetAppConfig() => throw Unexpected();
    public ModGenericTuple<IModConfigV1>[] GetActiveMods() => throw Unexpected();
    public WeakReference<T>[] MakeInterfaces<T>() where T : class => throw Unexpected();
    public void AddOrReplaceController<T>(IModV1 owner, T instance) => throw Unexpected();
    public void RemoveController<T>() => throw Unexpected();
    public WeakReference<T> GetController<T>() where T : class => throw Unexpected();
}
internal static class Program
{
    private static int Main()
    {
        var loader = new Loader(); var host = new Startup();
        host.Start(loader); host.Suspend(); host.Resume();
        if (loader.Logger.Messages.Count != 1 || !loader.Logger.Messages[0].Contains("Refused native activation")) throw new Exception("Missing refusal.");
        if (!host.CanUnload() || !host.CanSuspend()) throw new Exception("Dormant lifecycle contract mismatch.");
        host.Unload(); host.Unload();
        try { host.Start(loader); throw new Exception("Unloaded host restarted."); }
        catch (ObjectDisposedException) { }
        Console.WriteLine("PASS fake Reloaded host: refusal, logging-only capabilities, suspend/resume and idempotent unload");
        Console.WriteLine("{\"hostSmokePassed\":true,\"nativeRuntimeEnabled\":false,\"liveLoaderTested\":false}");
        return 0;
    }
}
