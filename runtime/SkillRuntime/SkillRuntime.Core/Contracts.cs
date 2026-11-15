using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;

namespace SkillRuntime.Core
{
    // Generations must come from a proven native adapter, never a reused pointer alone.
    public readonly struct ActorIdentity : IEquatable<ActorIdentity>
    {
        public ulong Battle { get; }
        public ulong Spawn { get; }
        public int Group { get; }
        public int Index { get; }
        public ActorIdentity(ulong battle, ulong spawn, int group, int index)
        {
            if (battle == 0 || spawn == 0 || group < 0 || index < 0)
                throw new ArgumentOutOfRangeException(nameof(battle), "Require proven nonzero generations and nonnegative identity.");
            Battle = battle; Spawn = spawn; Group = group; Index = index;
        }
        public bool Equals(ActorIdentity other) => Battle == other.Battle && Spawn == other.Spawn && Group == other.Group && Index == other.Index;
        public override bool Equals(object? obj) => obj is ActorIdentity other && Equals(other);
        public override int GetHashCode() => HashCode.Combine(Battle, Spawn, Group, Index);
    }

    public readonly struct ActionHandle : IEquatable<ActionHandle>
    {
        public ulong Battle { get; }
        public ulong Generation { get; }
        public ActionHandle(ulong battle, ulong generation)
        {
            if (battle == 0 || generation == 0) throw new ArgumentOutOfRangeException(nameof(generation));
            Battle = battle; Generation = generation;
        }
        public bool Equals(ActionHandle other) => Battle == other.Battle && Generation == other.Generation;
        public override bool Equals(object? obj) => obj is ActionHandle other && Equals(other);
        public override int GetHashCode() => HashCode.Combine(Battle, Generation);
    }

    public readonly struct ActionToken
    {
        internal Guid Owner { get; }
        internal ulong Sequence { get; }
        internal ActionToken(Guid owner, ulong sequence) { Owner = owner; Sequence = sequence; }
    }

    public sealed class ResolvedAction
    {
        public ActionHandle Handle { get; }
        public ActorIdentity Caster { get; }
        public ReadOnlyCollection<ActorIdentity> Targets { get; }
        public ResolvedAction(ActionHandle handle, ActorIdentity caster, IEnumerable<ActorIdentity> targets)
        {
            if (handle.Battle == 0 || handle.Generation == 0 || caster.Battle != handle.Battle || caster.Spawn == 0)
                throw new ArgumentException("Action and caster require the same valid battle generation.");
            if (targets == null) throw new ArgumentNullException(nameof(targets));
            var copy = new List<ActorIdentity>();
            foreach (var target in targets)
            {
                if (target.Battle != handle.Battle || target.Spawn == 0) throw new ArgumentException("Target belongs to another or invalid battle.");
                if (!copy.Contains(target)) copy.Add(target);
            }
            Handle = handle; Caster = caster; Targets = copy.AsReadOnly();
        }
    }

    [Flags] public enum PlacementChannels { None = 0, Position = 1, Yaw = 2 }
    public enum EndReason { Completed, Cancelled, ScriptAborted, BattleTeardown, ActorLost, TargetLost, BackendFault, Disposed }
    public readonly struct Point3
    {
        public float X { get; }
        public float Y { get; }
        public float Z { get; }
        public Point3(float x, float y, float z)
        {
            if (!Finite(x) || !Finite(y) || !Finite(z)) throw new ArgumentException("Position must be finite.");
            X = x; Y = y; Z = z;
        }
        internal static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    }
    public sealed class Placement
    {
        public Point3? Position { get; }
        public float? YawRadians { get; }
        public PlacementChannels Channels { get; }
        public Placement(Point3? position = null, float? yawRadians = null)
        {
            if (position == null && yawRadians == null) throw new ArgumentException("Placement cannot be empty.");
            if (yawRadians.HasValue && !Point3.Finite(yawRadians.Value)) throw new ArgumentException("Yaw must be finite.");
            Position = position; YawRadians = yawRadians;
            Channels = (position.HasValue ? PlacementChannels.Position : 0) | (yawRadians.HasValue ? PlacementChannels.Yaw : 0);
        }
    }

    public interface IPlacementLease
    {
        ActorIdentity Actor { get; }
        PlacementChannels Channels { get; }
        void Apply(Placement placement);
        // Exactly one call. If restoreAllowed is false, discard state without native writes.
        // Otherwise restore only owned channels and their proven setter/tween side effects.
        // Never restore a whole transform or unrelated animation/scale/visibility state.
        void Release(EndReason reason, bool restoreAllowed);
    }
    public interface IActionBackend
    {
        bool IsAvailable { get; }
        string UnavailableReason { get; }
        // Read from this current action, not last-command/global target state.
        ResolvedAction CaptureResolvedAction(ActionHandle handle);
        bool IsSameLiveActor(ActorIdentity identity);
        // Transactional: no placement writes. On exception, leave no retained ownership.
        IPlacementLease CapturePlacement(ActorIdentity actor, PlacementChannels channels);
    }
    public sealed class ReleaseReport
    {
        public bool AlreadyEnded { get; }
        public bool DeferredUntilApplyReturns { get; }
        public ReadOnlyCollection<string> Failures { get; }
        internal ReleaseReport(bool alreadyEnded, List<string>? failures = null, bool deferred = false)
        { AlreadyEnded = alreadyEnded; DeferredUntilApplyReturns = deferred; Failures = (failures ?? new List<string>()).AsReadOnly(); }
    }
}
