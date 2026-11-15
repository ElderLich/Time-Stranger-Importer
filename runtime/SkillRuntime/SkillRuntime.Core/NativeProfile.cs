using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Security.Cryptography;

namespace SkillRuntime.Core
{
    public sealed class NativeSeam
    {
        public string Name { get; }
        public uint Rva { get; }
        public NativeSeam(string name, uint rva) { Name = name; Rva = rva; }
    }
    public sealed class NativeProfile
    {
        public string Id { get; }
        public string ExecutableSha256 { get; }
        public ulong PreferredImageBase { get; }
        public ReadOnlyCollection<NativeSeam> ResearchSeams { get; }
        public NativeProfile(string id, string executableSha256, ulong preferredImageBase, IEnumerable<NativeSeam> researchSeams)
        {
            if (string.IsNullOrWhiteSpace(id) || executableSha256 == null || executableSha256.Length != 64)
                throw new ArgumentException("Profile requires an ID and full SHA256.");
            foreach (char c in executableSha256)
                if (!Uri.IsHexDigit(c)) throw new ArgumentException("Invalid SHA256.");
            Id = id; ExecutableSha256 = executableSha256.ToLowerInvariant(); PreferredImageBase = preferredImageBase;
            ResearchSeams = new List<NativeSeam>(researchSeams ?? throw new ArgumentNullException(nameof(researchSeams))).AsReadOnly();
        }
        public static NativeProfile TimeStrangerResearch => new NativeProfile(
            "time-stranger-ff9de825-research-v1",
            "ff9de825a543bf874cfb7e73ed951256d3ce4e8702957afa3b26ca6487a81688", 0x140000000,
            new[] {
                new NativeSeam("BattleLuaRegistration", 0xBC7000),
                new NativeSeam("DirectionScriptConstructor", 0x50B530),
                new NativeSeam("CopyAction", 0x50BAC0),
                new NativeSeam("SkillShootEntry", 0x50C2E0),
                new NativeSeam("ActionResultDispatch", 0xA02FB0),
                new NativeSeam("ResolveUnitDescriptor", 0x9E6520),
                new NativeSeam("PartyUnitDecoder", 0xC3F860),
                new NativeSeam("DirectionRemoval", 0xA23F30),
                new NativeSeam("DirectionScriptDestructor", 0x50BF50),
                new NativeSeam("DirectionManagerTeardown", 0xA236E0),
                new NativeSeam("CoroutineAbort", 0xBC1550),
                new NativeSeam("ScriptManagerReset", 0xBC08D0),
                new NativeSeam("ModelPosition", 0xA402F0),
                new NativeSeam("SetFigurePosition", 0xA437B0),
                new NativeSeam("SetActorRotation", 0xA43E20),
                new NativeSeam("SetFigureQuaternion", 0xA1BE20) });
    }
    public sealed class ProfileAssessment
    {
        public bool FingerprintMatches { get; }
        public string ObservedSha256 { get; }
        public ReadOnlyCollection<string> Refusals { get; }
        // Deliberately no enable flag, override, resolver or native call implementation.
        public bool RuntimeEnabled => false;
        internal ProfileAssessment(bool matches, string hash, List<string> refusals)
        { FingerprintMatches = matches; ObservedSha256 = hash; Refusals = refusals.AsReadOnly(); }
    }
    public static class NativeProfileValidator
    {
        public static readonly ReadOnlyCollection<string> UnprovenContracts = Array.AsReadOnly(new[] {
            "Native calling conventions, argument layouts, instruction signatures and hook lifetimes are not certified.",
            "Current-action resolved targets under redirection, counters and multiple hits are not certified.",
            "Action/coroutine identity across yield, abort and destruction is not certified.",
            "Actor spawn generations and attached/special model ownership are not certified.",
            "Exact owned position/yaw setter side effects and tween restoration are not certified.",
            "Action-scoped cleanup ordering and native clock/concurrent direction behavior are not certified.",
            "Dormant scaffold has no native backend, hooks or activation path."
        });
        public static ProfileAssessment Assess(string path, NativeProfile profile)
        {
            using (var file = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read)) return Assess(file, profile);
        }
        public static ProfileAssessment Assess(Stream stream, NativeProfile profile)
        {
            if (stream == null || !stream.CanRead || !stream.CanSeek) throw new ArgumentException("Require a readable seekable executable stream.");
            if (profile == null) throw new ArgumentNullException(nameof(profile));
            var failures = new List<string>();
            long original = stream.Position;
            string hash;
            try
            {
                stream.Position = 0;
                using (var sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                if (!string.Equals(hash, profile.ExecutableSha256, StringComparison.Ordinal)) failures.Add("Executable SHA256 does not match the pinned native profile.");
                using (var reader = new BinaryReader(stream, System.Text.Encoding.UTF8, true))
                {
                    try
                    {
                        if (stream.Length < 64) throw new InvalidDataException("DOS header is truncated.");
                        stream.Position = 0;
                        if (reader.ReadUInt16() != 0x5A4D) throw new InvalidDataException("Missing MZ signature.");
                        stream.Position = 0x3C; uint pe = reader.ReadUInt32();
                        if (pe < 64 || pe > stream.Length - 24) throw new InvalidDataException("PE header lies outside the file.");
                        stream.Position = pe;
                        if (reader.ReadUInt32() != 0x00004550) throw new InvalidDataException("Missing PE signature.");
                        if (reader.ReadUInt16() != 0x8664) throw new InvalidDataException("Native profile requires AMD64.");
                        stream.Position = pe + 20; ushort optionalSize = reader.ReadUInt16();
                        if (optionalSize < 64 || (long)pe + 24 + optionalSize > stream.Length) throw new InvalidDataException("Optional PE header is truncated.");
                        stream.Position = pe + 24;
                        if (reader.ReadUInt16() != 0x20B) throw new InvalidDataException("Native profile requires PE32+.");
                        stream.Position = pe + 24 + 24;
                        if (reader.ReadUInt64() != profile.PreferredImageBase) throw new InvalidDataException("Preferred image base differs from the pinned profile.");
                        stream.Position = pe + 24 + 56; uint imageSize = reader.ReadUInt32();
                        if (imageSize == 0) throw new InvalidDataException("Empty PE image.");
                        foreach (var seam in profile.ResearchSeams)
                            if (seam.Rva == 0 || seam.Rva >= imageSize) failures.Add("Research seam outside image: " + seam.Name);
                    }
                    catch (Exception ex) when (ex is EndOfStreamException || ex is InvalidDataException)
                    { failures.Add(ex.Message); }
                }
            }
            finally { stream.Position = original; }
            bool matches = failures.Count == 0;
            failures.AddRange(UnprovenContracts);
            return new ProfileAssessment(matches, hash, failures);
        }
    }
}
