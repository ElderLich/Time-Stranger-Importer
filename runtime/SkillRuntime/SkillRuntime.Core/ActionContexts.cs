using System;
using System.Collections.Generic;
using System.Threading;

namespace SkillRuntime.Core
{
    // Pure ownership state machine. It knows no game offsets, hooks, Lua states or clocks.
    public sealed class ActionContexts : IDisposable
    {
        private sealed class Context
        {
            public ResolvedAction Action { get; }
            public IPlacementLease? Lease;
            public bool Reserved;
            public bool Applying;
            public EndReason? PendingEnd;
            public Context(ResolvedAction action) { Action = action; }
        }
        private readonly IActionBackend _backend;
        private readonly Guid _owner = Guid.NewGuid();
        private readonly int _thread = Thread.CurrentThread.ManagedThreadId;
        private readonly Dictionary<ulong, Context> _contexts = new Dictionary<ulong, Context>();
        private readonly HashSet<ActionHandle> _seen = new HashSet<ActionHandle>();
        private readonly HashSet<ulong> _endedBattles = new HashSet<ulong>();
        private readonly HashSet<ActorIdentity> _quarantinedActors = new HashSet<ActorIdentity>();
        private readonly List<string> _cleanupFailures = new List<string>();
        private readonly Dictionary<ActorIdentity, ulong> _leases = new Dictionary<ActorIdentity, ulong>();
        private ulong _sequence;
        private bool _disposed;
        public ReleaseReport? DisposalReport { get; private set; }
        public IReadOnlyList<string> CleanupFailures => _cleanupFailures.AsReadOnly();
        public ActionContexts(IActionBackend backend) { _backend = backend ?? throw new ArgumentNullException(nameof(backend)); }

        private void CheckThread()
        {
            if (Thread.CurrentThread.ManagedThreadId != _thread) throw new InvalidOperationException("Action contexts require their owning game thread.");
        }
        private void CheckActive()
        {
            CheckThread();
            if (_disposed) throw new ObjectDisposedException(nameof(ActionContexts));
        }
        private void CheckOwner(ActionToken token)
        {
            if (token.Owner != _owner || token.Sequence == 0 || token.Sequence > _sequence)
                throw new ArgumentException("Foreign or invalid action token.");
        }
        private Context Get(ActionToken token)
        {
            CheckActive(); CheckOwner(token);
            if (!_contexts.TryGetValue(token.Sequence, out var context)) throw new InvalidOperationException("Action has ended.");
            if (context.PendingEnd.HasValue) throw new InvalidOperationException("Action is ending after its active backend call.");
            return context;
        }
        public ActionToken Begin(ActionHandle handle)
        {
            CheckActive();
            if (handle.Battle == 0 || handle.Generation == 0) throw new ArgumentException("Invalid action handle.");
            if (!_backend.IsAvailable) throw new NotSupportedException(_backend.UnavailableReason);
            if (_seen.Contains(handle) || _endedBattles.Contains(handle.Battle)) throw new InvalidOperationException("Action or battle generation already used/ended.");
            var action = _backend.CaptureResolvedAction(handle);
            if (action == null || !action.Handle.Equals(handle)) throw new InvalidOperationException("Backend returned a different action.");
            if (!_backend.IsSameLiveActor(action.Caster)) throw new InvalidOperationException("Caster generation is no longer live.");
            // Defend against a backend re-entering Begin during capture.
            CheckActive();
            if (_seen.Contains(handle) || _endedBattles.Contains(handle.Battle)) throw new InvalidOperationException("Action or battle changed during capture.");
            var id = checked(++_sequence);
            _contexts.Add(id, new Context(action)); _seen.Add(handle);
            return new ActionToken(_owner, id);
        }
        public ResolvedAction Inspect(ActionToken token) => Get(token).Action;

        public ActorIdentity RequireSingleTarget(ActionToken token)
        {
            var context = Get(token);
            if (context.Action.Targets.Count != 1) throw new NotSupportedException("Placement requires exactly one resolved action target; no first-target substitution.");
            return context.Action.Targets[0];
        }
        public void AcquirePlacement(ActionToken token, PlacementChannels channels)
        {
            var context = Get(token);
            if (channels == PlacementChannels.None || (channels & ~(PlacementChannels.Position | PlacementChannels.Yaw)) != 0)
                throw new ArgumentException("Explicit position and/or yaw ownership is required.");
            RequireSingleTarget(token);
            if (_quarantinedActors.Contains(context.Action.Caster)) throw new InvalidOperationException("Prior cleanup failed; this actor generation cannot acquire another placement lease.");
            if (context.Reserved || _leases.ContainsKey(context.Action.Caster)) throw new InvalidOperationException("Caster already has a placement owner.");
            EnsureLive(token, context);
            _leases.Add(context.Action.Caster, token.Sequence); context.Reserved = true;
            IPlacementLease? lease = null;
            try
            {
                if (!_backend.IsAvailable) throw new NotSupportedException(_backend.UnavailableReason);
                lease = _backend.CapturePlacement(context.Action.Caster, channels);
                if (lease == null || !lease.Actor.Equals(context.Action.Caster) || lease.Channels != channels)
                    throw new InvalidOperationException("Backend receipt does not match the requested actor/channels.");
                if (!_contexts.ContainsKey(token.Sequence)) throw new InvalidOperationException("Action ended during placement capture.");
                context.Lease = lease;
            }
            catch
            {
                if (context.Reserved)
                {
                    _leases.Remove(context.Action.Caster); context.Reserved = false;
                }
                // Capture is read-only; an unaccepted receipt must never write a restore.
                if (lease != null) lease.Release(EndReason.BackendFault, false);
                throw;
            }
        }
        private void EnsureLive(ActionToken token, Context context)
        {
            try
            {
                if (!_backend.IsAvailable) throw new NotSupportedException(_backend.UnavailableReason);
                if (!_backend.IsSameLiveActor(context.Action.Caster))
                { End(token, EndReason.ActorLost); throw new InvalidOperationException("Caster generation ended."); }
                foreach (var target in context.Action.Targets)
                    if (!_backend.IsSameLiveActor(target))
                    { End(token, EndReason.TargetLost); throw new InvalidOperationException("Resolved target generation ended."); }
                if (!_contexts.TryGetValue(token.Sequence, out var active) || !ReferenceEquals(active, context))
                    throw new InvalidOperationException("Action ended during backend validation.");
            }
            catch { End(token, EndReason.BackendFault); throw; }
        }
        public void Apply(ActionToken token, Placement placement)
        {
            var context = Get(token);
            if (placement == null) throw new ArgumentNullException(nameof(placement));
            var lease = context.Lease ?? throw new InvalidOperationException("No placement lease.");
            if (context.Applying) throw new InvalidOperationException("Reentrant placement application is not supported.");
            if ((placement.Channels & ~lease.Channels) != 0) throw new InvalidOperationException("Placement touches an unowned channel.");
            EnsureLive(token, context);
            context.Applying = true;
            try { lease.Apply(placement); }
            catch { End(token, EndReason.BackendFault); throw; }
            finally
            {
                context.Applying = false;
                if (context.PendingEnd.HasValue) End(token, context.PendingEnd.Value);
            }
        }
        public ReleaseReport End(ActionToken token, EndReason reason)
        {
            CheckThread(); CheckOwner(token);
            if (!_contexts.TryGetValue(token.Sequence, out var context)) return new ReleaseReport(true);
            if (context.Applying)
            {
                if (!context.PendingEnd.HasValue) context.PendingEnd = reason;
                return new ReleaseReport(false, deferred: true);
            }
            // Remove context first: destructor/abort re-entry cannot release twice.
            // Keep actor exclusivity through restoration so re-entry cannot acquire it early.
            _contexts.Remove(token.Sequence);
            var failures = new List<string>();
            var lease = context.Lease; context.Lease = null;
            if (lease != null)
            {
                bool restore = false;
                try { restore = _backend.IsSameLiveActor(context.Action.Caster); }
                catch (Exception ex) { failures.Add("Actor validity: " + ex.Message); }
                try { lease.Release(reason, restore); }
                catch (Exception ex) { failures.Add("Placement release: " + ex.Message); }
            }
            if (context.Reserved) { _leases.Remove(context.Action.Caster); context.Reserved = false; }
            if (failures.Count != 0) _quarantinedActors.Add(context.Action.Caster);
            foreach (var failure in failures) _cleanupFailures.Add("Action " + context.Action.Handle.Generation + ": " + failure);
            return new ReleaseReport(false, failures);
        }
        public ReleaseReport EndBattle(ulong battle)
        {
            CheckThread();
            _endedBattles.Add(battle);
            var tokens = new List<ActionToken>();
            foreach (var pair in _contexts)
                if (pair.Value.Action.Handle.Battle == battle) tokens.Add(new ActionToken(_owner, pair.Key));
            var failures = new List<string>();
            bool deferred = false;
            foreach (var token in tokens)
            {
                var result = End(token, EndReason.BattleTeardown);
                failures.AddRange(result.Failures); deferred |= result.DeferredUntilApplyReturns;
            }
            return new ReleaseReport(false, failures, deferred);
        }
        public void Dispose()
        {
            CheckThread(); if (_disposed) return;
            _disposed = true;
            var tokens = new List<ActionToken>();
            foreach (var id in _contexts.Keys) tokens.Add(new ActionToken(_owner, id));
            var failures = new List<string>();
            bool deferred = false;
            foreach (var token in tokens)
            {
                var result = End(token, EndReason.Disposed);
                failures.AddRange(result.Failures); deferred |= result.DeferredUntilApplyReturns;
            }
            DisposalReport = new ReleaseReport(false, failures, deferred);
        }
    }
}
