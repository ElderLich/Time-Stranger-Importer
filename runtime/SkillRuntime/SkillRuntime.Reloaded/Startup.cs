using System;
using Reloaded.Mod.Interfaces;
using Reloaded.Mod.Interfaces.Internal;
using SkillRuntime.Core;

namespace SkillRuntime.Reloaded
{
    // A separate Reloaded extension contract, intentionally never activated or installed.
    public sealed class Startup : IMod
    {
        private bool _unloaded;
        public Action Disposing { get; } = delegate { };
        public string Status { get; private set; } = "Dormant: no native backend or hooks.";
        public void Start(IModLoaderV1 loader)
        {
            if (_unloaded) throw new ObjectDisposedException(nameof(Startup));
            if (loader == null) throw new ArgumentNullException(nameof(loader));
            Status = "Refused native activation: target, ABI, ownership, tween and cleanup contracts remain unproven.";
            var logger = loader.GetLogger();
#pragma warning disable CS0618
            logger.PrintMessage("[SkillRuntime] " + Status, logger.ColorYellow);
#pragma warning restore CS0618
        }
        public void StartEx(IModLoaderV1 loader, IModConfigV1 config) => Start(loader);
        public ProfileAssessment AssessExecutable(string path) => NativeProfileValidator.Assess(path, NativeProfile.TimeStrangerResearch);
        public bool CanSuspend() => true;
        public bool CanUnload() => true;
        public void Suspend() { }
        public void Resume() { }
        public void Unload()
        {
            if (_unloaded) return;
            _unloaded = true; Status = "Unloaded dormant scaffold."; Disposing();
        }
    }
}
