// Adapted from rossalv/fortnite-chest-counter (MIT, see LICENSE-rossalv.txt).
// Changes: each chest is also labelled opened or broken (see Resolve), teammates' chests are
// counted from their search flag (see ResolveTeammates), and chests broken by placing a build are
// credited to whoever placed it (see ResolveBuildBreaks). With WantMap, players' movement and chest
// positions are decoded too, for the map window (see ResolveMap).
using System.Security.Cryptography;
using Microsoft.Extensions.Logging;
using Unreal.Core;
using Unreal.Core.Attributes;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;

namespace ChestReplayReader;

/// <summary>
/// The replay's spectator controller sends an extra NetPlayerIndex byte when its channel opens;
/// Unreal.Core needs this marker to skip it.
/// </summary>
[PlayerController("BP_ReplayPC_Athena_C")]
internal sealed class ReplayPlayerControllerMarker
{
}

/// <summary>
/// Reads a Fortnite .replay file and finds the chests opened by the player who recorded it.
///
/// How attribution works: every searchable chest replicates <c>SearchingPawn</c> (the pawn that
/// opened it) alongside <c>bAlreadySearched</c>. The game state replicates
/// <c>RecorderPlayerState</c>, and each player pawn replicates its <c>PlayerState</c>, so we can tell
/// exactly which chests were opened by the recording player's pawn(s).
///
/// Instead of a hard-coded list of chest blueprints (which change every season), any replicated
/// class whose name contains "Chest" is decoded by hand, reading only the few fields we need.
/// </summary>
public sealed class ChestReader : ReplayReader<Replay>
{
    private enum GroupKind { Ignore, GameState, Pawn, ReplayController, Chest, PlayerState, OtherContainer, Build, TeamInfo, SafeZone, Treasure }

    /// <summary>What we know about one player: team, platform, bot or not, and name.</summary>
    private sealed class PlayerInfo
    {
        public int? Team;
        public string? Platform;
        public bool Bot;
        public string? Name;
        /// <summary>The player's number in this match ("WorldPlayerId"); builds carry it as their owner.</summary>
        public int? WorldId;
    }

    private readonly Dictionary<uint, PlayerInfo> _players = new();

    /// <summary>The match's id: the same for every player's replay of one match.</summary>
    private string? _sessionId;

    private PlayerInfo Player(uint playerState)
    {
        if (!_players.TryGetValue(playerState, out var p))
        {
            _players[playerState] = p = new PlayerInfo();
        }

        return p;
    }

    private readonly record struct ChestUpdate(
        float Time, string ChestId, string ChestClass,
        uint? SearchingPawn, bool? Searched, bool? Destroyed, int? AnimCount)
    {
        public bool IsOpen => Searched == true || Destroyed == true || AnimCount > 0;
    }

    private readonly FrameClock _clock;
    private readonly Dictionary<string, GroupKind> _kinds = new(StringComparer.Ordinal);

    // Per-replay state (reset by Read).
    private uint? _recorderPlayerState;
    private uint? _controllerPlayerState;
    private readonly Dictionary<uint, uint> _pawnToPlayerState = new();
    private readonly List<ChestUpdate> _updates = new();
    private readonly Dictionary<uint, string> _dynamicChestIds = new();
    private readonly List<(string Class, double X, double Y, string Id)> _dynamicChests = new();

    /// <summary>Every change of a player's "bStartedInteractSearch" flag: on while they search something.</summary>
    private readonly List<(float Time, uint Pawn, bool On)> _searchFlags = new();

    /// <summary>Ammo boxes (and other non-chest containers) seen being searched: (time, searching pawn).</summary>
    private readonly List<(float Time, uint Pawn, string Cls)> _otherSearches = new();

    /// <summary>A player-placed build piece, from the first time it was seen.</summary>
    private sealed class BuildInfo
    {
        public float FirstSeen;
        public bool JustPlaced;   // still going up when first seen (not an old build coming into view)
        public int? Owner;        // the builder's WorldPlayerId
        public int? Team;
    }

    private readonly Dictionary<uint, BuildInfo> _builds = new();

    /// <summary>Also decode where everyone went (slower; only for the map window).</summary>
    public bool WantMap { get; init; }

    /// <summary>Read a replay Fortnite is still recording (mid-match): see <see cref="ReplayFile.ClearLiveFlag"/>.</summary>
    public bool AllowLive { get; init; }

    /// <summary>Pawn positions over time, at most one every <see cref="MoveStep"/> seconds per pawn.</summary>
    private readonly Dictionary<uint, List<(float T, float X, float Y, float Z)>> _moves = new();

    /// <summary>Pawn skydiving / parachute flags over time: landing = both off after either was on.</summary>
    private readonly List<(float T, uint Pawn, bool? Sky, bool? Chute)> _airFlags = new();

    /// <summary>Elimination count changes: (time, player state, new count).</summary>
    private readonly List<(float T, uint Ps, int Kills)> _killScores = new();

    /// <summary>Spawn position of chests whose actor came with one.</summary>
    private readonly Dictionary<string, (float X, float Y)> _chestSpawn = new();

    /// <summary>Development aid (CHEST_DUMP_FIELDS=1): print position-like field names of each class.</summary>
    private static readonly bool DumpFields = Environment.GetEnvironmentVariable("CHEST_DUMP_FIELDS") == "1";

    /// <summary>Debug (CT_TRACE="from-to", seconds): every property update in that stretch of the match.</summary>
    private static readonly (float From, float To)? Trace = Environment.GetEnvironmentVariable("CT_TRACE") is { } tr
        && tr.Split('-') is { Length: 2 } ab && float.TryParse(ab[0], out var tf) && float.TryParse(ab[1], out var tt)
        ? (tf, tt) : null;

    private bool Tracing => Trace is { } w && _clock.FrameTime >= w.From && _clock.FrameTime <= w.To;
    private readonly HashSet<string> _dumped = new();

    /// <summary>Movement mode changes per pawn (walking, falling, swimming...).</summary>
    private readonly List<(float T, uint Pawn, int Mode)> _modes = new();

    /// <summary>Map-placed chests seen in the recording (there in this match, opened or not).</summary>
    private readonly HashSet<string> _seenChests = new();

    /// <summary>The storm circle as last replicated: previous / next centre and radius, shrink times
    /// (server world time), and every change of it.</summary>
    private (double PX, double PY, double PR, double NX, double NY, double NR, double Start, double End) _zone;
    private readonly List<(float T, double PX, double PY, double PR, double NX, double NY, double NR, double Start, double End)> _zones = new();

    /// <summary>Server world time minus replay time, from the game state's clock (to place storm times).</summary>
    private readonly List<double> _worldOffsets = new();

    private int _zoneVectorFormat = -1;

    private const float MoveStep = 0.25f;
    private int _moveFormat = -1;
    public int MoveDecodeFailures { get; private set; }

    /// <summary>Two spawned chests of the same class closer than this (in cm) are the same chest.</summary>
    private const double SameSpawnTolerance = 150;

    public ChestReader() : this(new FrameClock())
    {
    }

    private ChestReader(FrameClock clock) : base(clock, ParseMode.Minimal)
    {
        _clock = clock;
    }

    /// <summary>Scan one replay. Never throws for bad/partial data; check <see cref="MatchResult.Error"/>.</summary>
    public MatchResult Read(string path)
    {
        var info = new FileInfo(path);
        var result = new MatchResult
        {
            FilePath = path,
            FileSize = info.Length,
            LastWriteUtc = info.LastWriteTimeUtc,
        };

        _recorderPlayerState = null;
        _controllerPlayerState = null;
        _players.Clear();
        _sessionId = null;
        _pawnToPlayerState.Clear();
        _updates.Clear();
        _dynamicChestIds.Clear();
        _dynamicChests.Clear();
        _searchFlags.Clear();
        _otherSearches.Clear();
        _builds.Clear();
        _treasures.Clear();
        _grants.Clear();
        _moves.Clear();
        _airFlags.Clear();
        _killScores.Clear();
        _chestSpawn.Clear();
        _teamSlots.Clear();
        _teamLocs.Clear();
        _modes.Clear();
        _seenChests.Clear();
        _zones.Clear();
        _zone = default;
        _worldOffsets.Clear();
        _clock.FrameTime = 0;

        try
        {
            var bytes = ReadShared(path);
            if (AllowLive)
            {
                ReplayFile.ClearLiveFlag(bytes);
            }

            using var archive = new Unreal.Core.BinaryReader(bytes);
            ReadReplay(archive);
            result.Complete = true;
        }
        catch (Exception ex)
        {
            // An in-progress replay usually ends in a half-written chunk. Keep everything read so far.
            result.Error = ex.Message;
            try { Cleanup(); } catch { /* reader state is reset on the next ReadReplay anyway */ }
        }

        result.ReadUpTo = _clock.FrameTime;
        if (Trace is not null)
        {
            foreach (var (pawn, ps) in _pawnToPlayerState)
            {
                Console.Error.WriteLine($"PAWN {pawn} = {(_players.TryGetValue(ps, out var pi) ? pi.Name : "?")}");
            }
        }  // match time of the last frame read (a live replay stops here)
        FillMetadata(result);
        Resolve(result);
        return result;
    }

    /// <summary>Fortnite keeps the replay open while recording, so allow concurrent writers.</summary>
    private static byte[] ReadShared(string path)
    {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        var buffer = new byte[fs.Length];
        fs.ReadExactly(buffer);
        return buffer;
    }

    private void FillMetadata(MatchResult result)
    {
        var replay = Replay;
        if (replay is null)
        {
            return;
        }

        if (replay.Info is { } info)
        {
            result.FriendlyName = info.FriendlyName?.Trim();
            result.LengthMs = info.LengthInMs;
            result.IsLive = info.IsLive;
            if (info.Timestamp > DateTime.MinValue)
            {
                // Written by the game as local time (matches the "UnsavedReplay-yyyy.MM.dd-HH.mm.ss" file name).
                result.MatchStartUtc = DateTime.SpecifyKind(info.Timestamp, DateTimeKind.Local).ToUniversalTime();
            }
        }

        if (replay.Header is { } header)
        {
            result.ReplayGuid = string.IsNullOrWhiteSpace(header.Guid) ? null : header.Guid;
            result.Branch = header.Branch;
        }
    }

    private void Resolve(MatchResult result)
    {
        var recorder = _recorderPlayerState ?? _controllerPlayerState;
        var myPawns = new HashSet<uint>();
        if (recorder is uint ps)
        {
            foreach (var (pawn, owner) in _pawnToPlayerState)
            {
                if (owner == ps)
                {
                    myPawns.Add(pawn);
                }
            }
        }

        result.RecorderFound = myPawns.Count > 0;

        foreach (var chest in _updates.GroupBy(u => u.ChestId))
        {
            var updates = chest.ToList();

            // Opened by anyone while we watched: someone was recorded searching it, or it flipped to searched.
            bool seenClosed = false, openedByAnyone = false;
            foreach (var u in updates)
            {
                if (u.SearchingPawn is > 0 && u.IsOpen) { openedByAnyone = true; break; }
                if (u.Searched == true && seenClosed) { openedByAnyone = true; break; }
                if (!u.IsOpen) seenClosed = true;
            }

            if (openedByAnyone)
            {
                result.ChestsOpenedByAnyone++;
            }

            // Opened or broken by me: my pawn is recorded as the searcher, and the chest is (or later becomes)
            // opened. When a chest is broken instead of opened, the server sets SearchingPawn to whoever broke it
            // and flips bDestroyed, so the same rule credits broken chests.
            var mine = updates.FindIndex(u => u.SearchingPawn is uint p && myPawns.Contains(p));
            var opened = mine < 0 ? -1 : updates.FindIndex(mine, u => u.IsOpen);
            if (opened >= 0)
            {
                // Broken = destroyed around the moment it opened, without the normal search finishing first.
                var at = updates[opened].Time;
                var window = updates.Skip(mine).Where(u => u.Time <= at + 0.5f).ToList();
                var broken = window.Any(u => u.Destroyed == true)
                             && !window.TakeWhile(u => u.Destroyed != true).Any(u => u.Searched == true || u.AnimCount > 0);
                var first = updates[mine];
                result.MyChests.Add(new ChestOpen(first.Time, first.ChestClass, first.ChestId, broken));
            }
        }

        var buildBreaks = ResolveBuildBreaks(recorder);
        foreach (var (time, cls, id) in buildBreaks.GetValueOrDefault(recorder ?? 0) ?? new())
        {
            result.MyChests.Add(new ChestOpen(time, cls, id, Broken: true, ByBuild: true));
        }

        result.MyChests.Sort((a, b) => a.Time.CompareTo(b.Time));
        ResolveTeammates(result, recorder, buildBreaks);
        ResolveTreasures(result, recorder);
        if (WantMap)
        {
            result.Map = ResolveMap(result, recorder, buildBreaks);
        }
    }

    /// <summary>A build placed this long before or after a chest breaks is what broke it (seconds). Measured:
    /// the build shows up within 0.08 s of the break; a wider window starts catching unrelated builds.</summary>
    private const float BuildBefore = 0.12f, BuildAfter = 0.06f;

    /// <summary>
    /// Chests broken by placing a build on them (a cone over a chest, a wall through it...).
    ///
    /// A chest broken with a pickaxe or a gun names its breaker (SearchingPawn), but one destroyed by a
    /// build placed on it names no one: it's just destroyed. Every build piece a player places is
    /// replicated though, with its team and its builder's WorldPlayerId ("OwnerPersistentID"), and it
    /// appears in the same frame the chest is destroyed. So a chest destroyed without anyone ever searching
    /// or hitting it, at the moment a freshly placed build appears, was broken by that build's owner.
    /// Only the recorder's team is credited. Returns {player state: [(time, class, chest id)]}.
    /// </summary>
    private Dictionary<uint, List<(float Time, string Class, string Id)>> ResolveBuildBreaks(uint? recorder)
    {
        var credited = new Dictionary<uint, List<(float, string, string)>>();
        if (recorder is not uint me || Player(me).Team is not int myTeam)
        {
            return credited;
        }

        var byWorldId = _players.Where(kv => kv.Value.WorldId is not null && kv.Value.Team == myTeam)
                                .ToDictionary(kv => kv.Value.WorldId!.Value, kv => kv.Key);
        var placed = _builds.Values.Where(b => b.JustPlaced && b.Owner is not null).OrderBy(b => b.FirstSeen).ToList();
        if (byWorldId.Count == 0 || placed.Count == 0)
        {
            return credited;
        }

        foreach (var chest in _updates.GroupBy(u => u.ChestId))
        {
            // Someone searched or hit it: then it's theirs (opened, or broken by hand) - see Resolve.
            if (chest.Any(u => u.SearchingPawn is > 0))
            {
                continue;
            }

            foreach (var u in chest.Where(u => u.Destroyed == true))
            {
                var build = placed.Where(b => b.FirstSeen >= u.Time - BuildBefore && b.FirstSeen <= u.Time + BuildAfter)
                                  .MinBy(b => Math.Abs(b.FirstSeen - u.Time));
                if (build is null)
                {
                    continue;
                }

                if (build.Team == myTeam && byWorldId.TryGetValue(build.Owner!.Value, out var builder))
                {
                    if (!credited.TryGetValue(builder, out var list)) credited[builder] = list = new();
                    list.Add((u.Time, u.ChestClass, u.ChestId));
                }

                break;
            }
        }

        return credited;
    }

    /// <summary>
    /// The recorder's teammates and the chests each of them opened or broke.
    ///
    /// Chests only replicate while they are near the recorder (or whoever the recorder is spectating),
    /// so a teammate's chests far away never show up as chest updates. Teammates' own characters do
    /// replicate all match long, and each carries a "bStartedInteractSearch" flag that is on while they
    /// search something. A search that runs its full time (about 0.75 s) is a container opened, and in
    /// the recorder's own data about 94% of those are chests (the rest ammo boxes and such).
    /// So: opened = the larger of (chests seen opened near the recorder, full searches that were seen
    /// to be chests + unseen full searches x the recorder's chest share). Broken chests have no
    /// search, so only the ones broken near the recorder are known.
    /// </summary>
    /// <summary>A treasure hunt gold chest: spawned when a chest rolls a prize, gone ~1.5 s later.</summary>
    private sealed class TreasureInfo
    {
        public float FirstSeen;
        public int? Owner;          // the opener's WorldPlayerId ("OwnerPersistentID")
        public uint? OpeningPawn;
        public bool? Won;           // "bSuccess"
        public string? Prize;       // "DelilahPrize:dpid_item21", or a bare GUID (Pirate Shop access)
        public uint? Source;        // the chest it came out of ("SourceContainer")
    }

    private readonly Dictionary<uint, TreasureInfo> _treasures = new();

    /// <summary>Treasure prize grants: (time, channel actor = the player's state, fast array element, handler).</summary>
    private readonly List<(float Time, uint Actor, int Element, string Handler)> _grants = new();

    /// <summary>
    /// A treasure prize (gizmo or cosmetic) grants a "GA_TreasureHandler_*" ability on the player, sent in the
    /// ActivatableAbilities fast array of their AbilitySystemComponent. Unreal.Core has no type for it, so the
    /// delta is read from a copy of its raw bits: header, then each changed element's properties; a property
    /// that is an object reference to a TreasureHandler class is a grant. Decoder after LED-N/Fortnite-ChestCounter
    /// (MIT). Anything that doesn't parse is ignored.
    /// </summary>
    private void ReadAbilityGrants(uint channel, byte[] data, int bits)
    {
        var pos = 1; // bSupportsFastArrayDeltaStructSerialization
        int Bit() => pos < bits ? (data[pos >> 3] >> (pos & 7)) & 1 : throw new IndexOutOfRangeException();
        uint Read(int n) { uint v = 0; for (var k = 0; k < n; k++) { v |= (uint)Bit() << k; pos++; } return v; }
        uint ReadPacked()
        {
            uint v = 0;
            for (var shift = 0; shift < 35; shift += 7)
            {
                var b = Read(8);
                v |= (b >> 1) << shift;
                if ((b & 1) == 0) return v;
            }

            throw new InvalidDataException();
        }

        try
        {
            _ = Read(32); _ = Read(32); // replication keys
            var deletes = Read(32);
            var changed = Read(32);
            if (deletes > 512 || changed > 512)
            {
                return;
            }

            pos += (int)deletes * 32;
            for (var i = 0; i < changed; i++)
            {
                var element = (int)Read(32);
                while (true)
                {
                    var handle = ReadPacked();
                    if (handle == 0) break;
                    var numBits = (int)ReadPacked();
                    if (numBits < 0 || pos + numBits > bits) return;
                    if (numBits is 8 or 16 or 24 or 32 or 40)
                    {
                        var start = pos;
                        uint guid = 0;
                        for (var shift = 0; pos < start + numBits; shift += 7)
                        {
                            var b = Read(8);
                            guid |= (b >> 1) << shift;
                            if ((b & 1) == 0) break;
                        }

                        pos = start + numBits;
                        if (_netGuidCache.NetGuidToPathName.TryGetValue(guid, out var path)
                            && path.Contains("TreasureHandler", StringComparison.Ordinal))
                        {
                            var actor = Channels[channel]?.ActorId ?? 0;
                            if (!_grants.Any(g => g.Actor == actor && g.Element == element))
                            {
                                var dot = path.LastIndexOf('.');
                                _grants.Add((_clock.FrameTime, actor, element, (dot >= 0 ? path[(dot + 1)..] : path).Replace("Default__", "")));
                            }
                        }
                    }
                    else
                    {
                        pos += numBits;
                    }
                }
            }
        }
        catch (Exception e) when (e is IndexOutOfRangeException or InvalidDataException)
        {
            // not this layout: ignore
        }
    }

    /// <summary>Debug: the treasure grants seen (time, actor, handler).</summary>
    public IReadOnlyList<(float Time, uint Actor, int Element, string Handler)> Grants => _grants;

    /// <summary>
    /// The prize id inside "PrizeTemplateId": a "Table:row" name such as "DelilahPrize:dpid_item21", or a
    /// bare GUID (the Pirate Shop access prize). It's a struct, so look for the text in its bytes.
    /// If neither shows up, keep the raw bytes as hex so new kinds of prizes can still be told apart.
    /// </summary>
    private static string? ReadPrizeId(FBitArchive archive, int numBits)
    {
        var bytes = new byte[(numBits + 7) / 8];
        for (var i = 0; i < numBits; i++)
        {
            if (archive.ReadBit()) bytes[i / 8] |= (byte)(1 << (i % 8));
        }

        var text = new string(bytes.Select(c => c >= 32 && c < 127 ? (char)c : ' ').ToArray());
        var m = System.Text.RegularExpressions.Regex.Match(text, @"[A-Za-z][A-Za-z0-9_]*:[A-Za-z0-9_.\-]+");
        if (m.Success) return m.Value;
        m = System.Text.RegularExpressions.Regex.Match(text, @"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}");
        if (m.Success) return m.Value.ToLowerInvariant();
        return bytes.All(b => b == 0) ? null : "hex:" + Convert.ToHexString(bytes);
    }

    /// <summary>
    /// Treasure hunt prizes won by the recorder's team, from the gold chests seen in this replay. Each gold
    /// chest names its opener (WorldPlayerId, and their character) and the prize. Like chests, it's only
    /// in the replay when it was near the recorder.
    /// </summary>
    private void ResolveTreasures(MatchResult result, uint? recorder)
    {
        if (recorder is not uint me)
        {
            return;
        }

        var myTeam = Player(me).Team;
        foreach (var t in _treasures.Values.OrderBy(t => t.FirstSeen))
        {
            uint? opener = null;
            if (t.Owner is int w)
            {
                opener = _players.Where(kv => kv.Value.WorldId == w).Select(kv => (uint?)kv.Key).FirstOrDefault();
            }

            if (opener is null && t.OpeningPawn is uint pawn && _pawnToPlayerState.TryGetValue(pawn, out var ps))
            {
                opener = ps;
            }

            var info = opener is uint o ? Player(o) : null;
            var who = opener == me ? "me" : info is not null && myTeam is not null && info.Team == myTeam ? "teammate" : "other";
            string? from = null;
            if (t.Source is uint chest)
            {
                // a map-placed chest has a path name; a spawned one (buried, relic...) was seen as a chest update
                from = _netGuidCache.TryGetPathName(chest, out var path) ? path
                     : _dynamicChestIds.TryGetValue(chest, out var dyn) ? dyn : $"#{chest}";
                var dot = from.LastIndexOf('.');
                from = dot >= 0 ? from[(dot + 1)..] : from;
            }

            result.Treasures.Add(new TreasureFind(t.FirstSeen, t.Prize, t.Won ?? true, info?.Name, info?.Platform, who, from));
        }

        // Sprite gizmo prizes skip the gold chest: all that shows is the prize grant on the player (see
        // ReadAbilityGrants). A grant with a gold chest at the same moment is that gold chest's prize.
        // Which gizmo it was isn't in the replay (the inventory isn't recorded, and every gizmo name is
        // loaded at the start); and the handler may be granted only once a match, so later gizmos can be missed.
        foreach (var g in _grants.OrderBy(g => g.Time))
        {
            if (result.Treasures.Any(t => Math.Abs(t.Time - g.Time) <= 5))
            {
                continue;
            }

            // The prize is shared with the whole squad (the grant lands on everyone), so it belongs to whoever
            // opened or broke a chest right then; a buried chest grants ~2.5 s before it reports searched.
            // No chest in view: a teammate far from the recorder found it (who exactly isn't known).
            var chest = _updates.Where(u => u.Time >= g.Time - 3 && u.Time <= g.Time + 4 && u.SearchingPawn is > 0 && u.IsOpen)
                                .OrderBy(u => Math.Abs(u.Time - g.Time)).FirstOrDefault();
            PlayerInfo? info = null;
            var who = "teammate";
            if (chest.SearchingPawn is uint pawn && _pawnToPlayerState.TryGetValue(pawn, out var opener))
            {
                info = Player(opener);
                who = opener == me ? "me" : myTeam is not null && info.Team == myTeam ? "teammate" : "other";
            }

            if (who == "other")
            {
                continue;  // someone else's chest happened to open then: not our squad's prize
            }

            result.Treasures.Add(new TreasureFind(g.Time, "gizmo", true, info?.Name, info?.Platform, who,
                                                  chest.ChestId is { } id ? ClassName(id) : null));
        }
    }

    private void ResolveTeammates(MatchResult result, uint? recorder,
                                  Dictionary<uint, List<(float Time, string Class, string Id)>> buildBreaks)
    {
        if (recorder is not uint me)
        {
            return;
        }

        result.SessionId = _sessionId;
        var myInfo = Player(me);
        result.RecorderName = myInfo.Name;
        result.RecorderPlatform = myInfo.Platform;
        result.RecorderSearches = CompletedSearches(PawnsOf(me)).Count;
        // What share of full searches are chests (the rest: ammo boxes and such), from the recorder,
        // whose every search is in view. Across a season of matches this was 94%.
        var myOpened = result.MyChests.Count(c => !c.Broken);
        var chestShare = result.RecorderSearches >= 10
            ? Math.Clamp((double)myOpened / result.RecorderSearches, 0.8, 1.0)
            : 0.94;
        if (myInfo.Team is not int myTeam)
        {
            return;
        }

        var mates = _players.Where(kv => kv.Key != me && kv.Value.Team == myTeam).ToList();
        var chests = _updates.GroupBy(u => u.ChestId).Select(g => g.ToList()).ToList();
        foreach (var (ps, info) in mates)
        {
            var pawns = PawnsOf(ps);
            int opened = 0, broken = 0;
            foreach (var updates in chests)
            {
                var first = updates.FindIndex(u => u.SearchingPawn is uint p && pawns.Contains(p));
                var open = first < 0 ? -1 : updates.FindIndex(first, u => u.IsOpen);
                if (open < 0)
                {
                    continue;
                }

                var at = updates[open].Time;
                var window = updates.Skip(first).Where(u => u.Time <= at + 0.5f).ToList();
                var wasBroken = window.Any(u => u.Destroyed == true)
                                && !window.TakeWhile(u => u.Destroyed != true).Any(u => u.Searched == true || u.AnimCount > 0);
                if (wasBroken) broken++; else opened++;
            }

            // Each full search: a chest seen opened by them, an ammo box seen searched by them, or (far
            // away) unseen. Unseen ones count as chests in the same proportion as the recorder's own
            // full searches in this match turned out to be chests. (The tracker refines this with the
            // map data: chests credited to them that match none of their searches by location.)
            var searches = CompletedSearches(pawns);
            int seenChest = 0, seenOther = 0, unseen = 0;
            foreach (var s in searches)
            {
                bool Near(float t) => t >= s.Start - 0.5f && t <= s.End + 1.5f;
                if (_updates.Any(u => u.SearchingPawn is uint p && pawns.Contains(p) && u.Searched == true && Near(u.Time)))
                    seenChest++;
                else if (_otherSearches.Any(o => pawns.Contains(o.Pawn) && Near(o.Time)))
                    seenOther++;
                else
                    unseen++;
            }

            var fromSearches = seenChest + (int)Math.Round(unseen * chestShare);
            var byBuild = buildBreaks.TryGetValue(ps, out var bb) ? bb.Count : 0;
            result.Teammates.Add(new Teammate(info.Name, info.Platform, info.Bot, Math.Max(opened, fromSearches),
                                              broken + byBuild, searches.Count, opened, byBuild));
        }
    }

    /// <summary>Where this set of pawns was at time t: the closest sample within <paramref name="within"/> seconds.</summary>
    private (float X, float Y)? PositionAt(IEnumerable<uint> pawns, float t, float within = 3f)
    {
        (float X, float Y)? best = null;
        var bestGap = within;
        foreach (var pawn in pawns)
        {
            if (!_moves.TryGetValue(pawn, out var path) || path.Count == 0) continue;
            int lo = 0, hi = path.Count - 1;
            while (lo < hi)
            {
                var mid = (lo + hi) / 2;
                if (path[mid].T < t) lo = mid + 1; else hi = mid;
            }

            for (var i = lo - 1; i <= lo; i++)
            {
                if (i < 0 || i >= path.Count) continue;
                var gap = Math.Abs(path[i].T - t);
                if (gap <= bestGap)
                {
                    bestGap = gap;
                    best = (path[i].X, path[i].Y);
                }
            }
        }

        return best;
    }

    private static float R(float v) => MathF.Round(v);

    /// <summary>Squad members' team-info positions are stored in _moves under this + their player state id.</summary>
    private const uint TeamKeyBase = 0x8000_0000;

    /// <summary>Routes, landing spots, kill spots and chest positions for the recorder's squad, plus every
    /// map-placed chest seen opened by anyone (for the chest-spot database).</summary>
    private MapData ResolveMap(MatchResult result, uint? recorder,
                               Dictionary<uint, List<(float Time, string Class, string Id)>> buildBreaks)
    {
        var map = new MapData();
        if (recorder is not uint me)
        {
            return map;
        }

        var squad = new List<(uint Ps, PlayerInfo Info, bool Rec)> { (me, Player(me), true) };
        if (Player(me).Team is int myTeam)
        {
            squad.AddRange(_players.Where(kv => kv.Key != me && kv.Value.Team == myTeam).Select(kv => (kv.Key, kv.Value, false)));
        }

        var chests = _updates.GroupBy(u => u.ChestId).Select(g => g.ToList()).ToList();
        var squadPawns = squad.SelectMany(s => PawnsOf(s.Ps)).ToHashSet();
        var taken = new HashSet<uint>();  // each eliminated player is someone's victim only once
        foreach (var (ps, info, rec) in squad)
        {
            var pawns = PawnsOf(ps);
            var where = pawns;  // where to look up positions
            var fromTeam = _teamLocs.Where(l => l.Ps == ps).Select(l => (l.T, l.X, l.Y, l.Z)).ToList();
            if (!rec && fromTeam.Count > 0)
            {
                var key = TeamKeyBase + ps;
                _moves[key] = fromTeam;
                where = new HashSet<uint> { key };
            }
            var samples = where.Where(_moves.ContainsKey).SelectMany(p => _moves[p]).OrderBy(m => m.T).ToList();
            var route = new List<float[]>();
            var modes = _modes.Where(x => pawns.Contains(x.Pawn)).OrderBy(x => x.T).ToList();
            var mi = 0;
            var current = 0;
            foreach (var m in samples)
            {
                while (mi < modes.Count && modes[mi].T <= m.T) current = modes[mi++].Mode;
                if (route.Count == 0 || m.T - route[^1][0] >= 1f)
                {
                    route.Add(new[] { MathF.Round(m.T, 1), R(m.X), R(m.Y), R(m.Z), current });
                }
            }

            // Landing: the first moment, after skydiving or gliding, that neither is on.
            float[]? landing = null;
            bool inAir = false, sky = false, chute = false;
            foreach (var f in _airFlags.Where(f => pawns.Contains(f.Pawn)).OrderBy(f => f.T))
            {
                sky = f.Sky ?? sky;
                chute = f.Chute ?? chute;
                if (sky || chute)
                {
                    inAir = true;
                }
                else if (inAir && PositionAt(where, f.T) is { } at)
                {
                    landing = new[] { MathF.Round(f.T, 1), R(at.X), R(at.Y) };
                    break;
                }
            }

            var kills = new List<MapKill>();
            var count = 0;
            foreach (var k in _killScores.Where(k => k.Ps == ps).OrderBy(k => k.T))
            {
                if (k.Kills > count && PositionAt(where, k.T, 5f) is { } at)
                {
                    // Several at once: a squad wiped out together (its knocked players go down with the last
                    // one), or a far-away teammate's count arriving late in one batch. Enemies vanishing next
                    // to them right then tell the first; with none, time and place are only rough.
                    var approx = false;
                    for (var i = count; i < k.Kills && i < count + 10; i++)
                    {
                        var victim = approx ? null : FindVictim(k.T, at, squadPawns, taken);
                        if (victim is null && !rec && k.Kills - count > 1 && i == count)
                        {
                            approx = true;
                        }

                        if (approx)
                        {
                            kills.Add(new MapKill(MathF.Round(k.T, 1), R(at.X), R(at.Y), null, null, null, Approx: true));
                            continue;
                        }

                        kills.Add(new MapKill(MathF.Round(k.T, 1), R(at.X), R(at.Y),
                                              victim is null ? null : R(victim.Value.X), victim is null ? null : R(victim.Value.Y),
                                              victim?.Name));
                    }
                }

                count = Math.Max(count, k.Kills);
            }

            float[]? end = samples.Count > 0 ? new[] { MathF.Round(samples[^1].T, 1), R(samples[^1].X), R(samples[^1].Y) } : null;
            map.Players.Add(new MapPlayer(info.Name, rec, route, landing, kills, end));

            // Chests they opened or broke while in the recorder's view.
            var seenTimes = new List<float>();
            foreach (var updates in chests)
            {
                var first = updates.FindIndex(u => u.SearchingPawn is uint p && pawns.Contains(p));
                var open = first < 0 ? -1 : updates.FindIndex(first, u => u.IsOpen);
                if (open < 0) continue;
                var at = updates[open].Time;
                var window = updates.Skip(first).Where(u => u.Time <= at + 0.5f).ToList();
                var broken = window.Any(u => u.Destroyed == true)
                             && !window.TakeWhile(u => u.Destroyed != true).Any(u => u.Searched == true || u.AnimCount > 0);
                var u0 = updates[first];
                seenTimes.Add(u0.Time);
                AddChest(map, info.Name, u0.Time, u0.ChestId, u0.ChestClass, broken, false, where);
            }

            foreach (var (time, cls, id) in buildBreaks.GetValueOrDefault(ps) ?? new())
            {
                AddChest(map, info.Name, time, id, cls, true, true, where);
            }

            // A teammate's full searches far from the recorder: no chest data, only where they stood.
            if (!rec)
            {
                foreach (var srch in CompletedSearches(pawns))
                {
                    bool Near(float t) => t >= srch.Start - 0.5f && t <= srch.End + 1.5f;
                    if (seenTimes.Any(Near) || _otherSearches.Any(o => pawns.Contains(o.Pawn) && Near(o.Time))) continue;
                    if (_updates.Any(u => u.SearchingPawn is uint p && pawns.Contains(p) && Near(u.Time))) continue;
                    if (PositionAt(where, srch.Start) is { } at)
                    {
                        map.Chests.Add(new MapChest(MathF.Round(srch.Start, 1), info.Name, null, null, false, false, R(at.X), R(at.Y), "search"));
                    }
                }
            }

            if (Environment.GetEnvironmentVariable("CT_SEARCH_LOG") == "1")
            {
                map.Searches ??= new();
                foreach (var srch in CompletedSearches(pawns))
                {
                    bool Near(float t) => t >= srch.Start - 0.5f && t <= srch.End + 1.5f;
                    var kind = seenTimes.Any(Near) || _updates.Any(u => u.SearchingPawn is uint p && pawns.Contains(p) && Near(u.Time)) ? "chest"
                        : _otherSearches.FirstOrDefault(o => pawns.Contains(o.Pawn) && Near(o.Time)) is { Cls: { } oc } ? "other:" + oc : "unseen";
                    var at = PositionAt(where, srch.Start);
                    map.Searches.Add(new MapSearch(info.Name ?? "", MathF.Round(srch.Start, 2), MathF.Round(srch.End - srch.Start, 3), kind,
                                                   at is { } a1 ? R(a1.X) : 0, at is { } a2 ? R(a2.Y) : 0));
                }

                foreach (var o in _otherSearches.Where(o => pawns.Contains(o.Pawn)))
                {
                    if (PositionAt(where, o.Time, 1.5f) is { } op)
                        map.Searches.Add(new MapSearch(info.Name ?? "", MathF.Round(o.Time, 2), 0, "container:" + o.Cls, R(op.X), R(op.Y)));
                }
            }
        }

        // Every map-placed chest anyone was seen opening: where the opener stood (or the chest's own position).
        foreach (var updates in chests)
        {
            var id = updates[0].ChestId;
            if (id.StartsWith('#') || id.Contains('@')) continue; // no stable name: spawned chests
            if (_chestSpawn.TryGetValue(id, out var spawn))
            {
                map.Spots.Add(new MapSpot(id, updates[0].ChestClass, R(spawn.X), R(spawn.Y), "actor"));
                continue;
            }

            foreach (var u in updates)
            {
                if (u.SearchingPawn is uint opener && opener > 0 && u.IsOpen
                    && PositionAt(new[] { opener }, u.Time, 1.5f) is { } at)
                {
                    map.Spots.Add(new MapSpot(id, updates[0].ChestClass, R(at.X), R(at.Y), "opener"));
                    break;
                }
            }
        }

        map.Chests.Sort((a, b) => a.T.CompareTo(b.T));
        map.Seen = _seenChests.OrderBy(x => x, StringComparer.Ordinal).ToList();
        map.Storm = ResolveStorm();
        return map;
    }

    /// <summary>Storm phases in replay time. Shrink times are server world time: the game state's clock
    /// gives the offset. One entry per distinct phase (the indicator re-sends unchanged values).</summary>
    private List<MapStorm> ResolveStorm()
    {
        var phases = new List<MapStorm>();
        if (_worldOffsets.Count == 0)
        {
            return phases;
        }

        var sorted = _worldOffsets.OrderBy(x => x).ToList();
        var offset = sorted[sorted.Count / 2];
        foreach (var z in _zones)
        {
            if (z.NR <= 0 || z.End <= 0 || z.End < z.Start) continue;
            var start = (float)(z.Start - offset);
            var end = (float)(z.End - offset);
            if (phases.Count > 0)
            {
                var last = phases[^1];
                if (MathF.Abs(last.NR - (float)z.NR) < 1 && MathF.Abs(last.End - end) < 1)
                {
                    // same phase: keep the fullest information (previous circle may arrive later)
                    if (last.PR <= 0 && z.PR > 0)
                        phases[^1] = last with { PX = R((float)z.PX), PY = R((float)z.PY), PR = R((float)z.PR) };
                    continue;
                }
            }

            phases.Add(new MapStorm(MathF.Round(z.T, 1), R((float)z.PX), R((float)z.PY), R((float)z.PR),
                                    R((float)z.NX), R((float)z.NY), R((float)z.NR), MathF.Round(start, 1), MathF.Round(end, 1)));
        }

        return phases;
    }

    /// <summary>
    /// The player eliminated at time t near (killer): an enemy pawn whose movement stops for good around
    /// then (an eliminated player's character is removed), the closest one within 300 m. Null if none.
    /// </summary>
    private (float X, float Y, string? Name)? FindVictim(float t, (float X, float Y) killer, HashSet<uint> squadPawns,
                                                          HashSet<uint> taken)
    {
        (float X, float Y, string? Name, uint Pawn)? best = null;
        var bestDist = 30_000f;
        foreach (var (pawn, path) in _moves)
        {
            if (pawn >= TeamKeyBase || squadPawns.Contains(pawn) || taken.Contains(pawn) || path.Count == 0) continue;
            var last = path[^1];
            if (last.T < t - 3f || last.T > t + 5f) continue;
            var dist = MathF.Sqrt((last.X - killer.X) * (last.X - killer.X) + (last.Y - killer.Y) * (last.Y - killer.Y));
            if (dist < bestDist)
            {
                bestDist = dist;
                var name = _pawnToPlayerState.TryGetValue(pawn, out var vps) ? Player(vps).Name : null;
                best = (last.X, last.Y, name, pawn);
            }
        }

        if (best is not { } b) return null;
        taken.Add(b.Pawn);
        return (b.X, b.Y, b.Name);
    }

    private void AddChest(MapData map, string? who, float time, string id, string cls, bool broken, bool byBuild, HashSet<uint> pawns)
    {
        if (_chestSpawn.TryGetValue(id, out var spawn))
        {
            map.Chests.Add(new MapChest(MathF.Round(time, 1), who, id, cls, broken, byBuild, R(spawn.X), R(spawn.Y), "actor"));
        }
        else if (PositionAt(pawns, time) is { } at)
        {
            map.Chests.Add(new MapChest(MathF.Round(time, 1), who, id, cls, broken, byBuild, R(at.X), R(at.Y), "opener"));
        }
    }

    private HashSet<uint> PawnsOf(uint playerState) =>
        _pawnToPlayerState.Where(kv => kv.Value == playerState).Select(kv => kv.Key).ToHashSet();

    /// <summary>Shortest and longest time a finished search takes, in seconds (measured: 0.55 to 1.25).</summary>
    private const float MinSearch = 0.5f, MaxSearch = 2.5f;

    /// <summary>Searches these pawns ran to the end: the flag came on, then went off after a full search.
    /// (A flag switched off sooner was an interrupted search; one that stays on was not finished.)</summary>
    private List<(float Start, float End)> CompletedSearches(HashSet<uint> pawns)
    {
        var done = new List<(float, float)>();
        foreach (var pawn in pawns)
        {
            float? since = null;
            foreach (var (time, p, on) in _searchFlags)
            {
                if (p != pawn)
                {
                    continue;
                }

                if (on)
                {
                    since ??= time;
                }
                else if (since is float start)
                {
                    var length = time - start;
                    if (length >= MinSearch && length <= MaxSearch)
                    {
                        done.Add((start, time));
                    }

                    since = null;
                }
            }
        }

        return done;
    }

    private GroupKind Classify(string path)
    {
        if (_kinds.TryGetValue(path, out var kind))
        {
            return kind;
        }

        kind = GroupKind.Ignore;
        // Skip RPC parameter groups ("Class:Function") and ClassNetCache groups.
        if (!path.Contains(':') && !path.EndsWith("ClassNetCache", StringComparison.Ordinal))
        {
            var dot = path.LastIndexOf('.');
            var name = dot >= 0 ? path[(dot + 1)..] : path;

            if (name == "FortTeamPrivateInfo")
                kind = GroupKind.TeamInfo;
            else if (name.StartsWith("SafeZoneIndicator", StringComparison.Ordinal))
                kind = GroupKind.SafeZone;
            else if (name.Contains("TreasureHunt", StringComparison.Ordinal))  // the gold prize chest (see ResolveTreasures)
                kind = GroupKind.Treasure;
            else if (name.Contains("Chest", StringComparison.OrdinalIgnoreCase))
                kind = GroupKind.Chest;
            else if (name.StartsWith("Tiered_Ammo", StringComparison.Ordinal) || name.Contains("AmmoBox", StringComparison.Ordinal)
                     || path.Contains("/Containers/", StringComparison.Ordinal))  // fishing rod barrels, food boxes...
                kind = GroupKind.OtherContainer;
            else if (name.StartsWith("PBWA_", StringComparison.Ordinal))  // player-built walls, floors, cones...
                kind = GroupKind.Build;
            else if (name.Contains("PlayerPawn", StringComparison.Ordinal))
                kind = GroupKind.Pawn;
            else if (name.Contains("ReplayPC", StringComparison.Ordinal))
                kind = GroupKind.ReplayController;
            else if (name.Contains("GameState", StringComparison.Ordinal) && !name.Contains("Component", StringComparison.Ordinal))
                kind = GroupKind.GameState;
            else if (name.Contains("PlayerState", StringComparison.Ordinal) && !name.Contains("Component", StringComparison.Ordinal))
                kind = GroupKind.PlayerState;
        }

        _kinds[path] = kind;
        return kind;
    }

    /// <summary>
    /// Map-placed chests have a stable actor name. Spawned chests (buried, relic, booty, ...) don't, and
    /// the recording client gives them a new network id every time they come back into view, so identify
    /// those by class + spawn position instead.
    /// </summary>
    private string ChestId(uint actor, FVector? location, string groupPath)
    {
        if (_netGuidCache.TryGetPathName(actor, out var name))
        {
            return name;
        }

        if (_dynamicChestIds.TryGetValue(actor, out var known))
        {
            return known;
        }

        string id;
        if (location is null)
        {
            id = $"#{actor}";
        }
        else
        {
            // Match horizontally only: buried chests rise out of the ground, so their Z changes.
            var cls = ClassName(groupPath);
            var match = _dynamicChests.FindIndex(c => c.Class == cls
                && Math.Abs(c.X - location.X) < SameSpawnTolerance
                && Math.Abs(c.Y - location.Y) < SameSpawnTolerance);
            if (match >= 0)
            {
                id = _dynamicChests[match].Id;
            }
            else
            {
                id = $"{cls}@{location.X:F0},{location.Y:F0}";
                _dynamicChests.Add((cls, location.X, location.Y, id));
            }
        }

        _dynamicChestIds[actor] = id;
        return id;
    }

    private static string ClassName(string path)
    {
        var dot = path.LastIndexOf('.');
        var name = dot >= 0 ? path[(dot + 1)..] : path;
        return name.EndsWith("_C", StringComparison.Ordinal) ? name[..^2] : name;
    }

    /// <summary>
    /// Mirrors Unreal.Core's property reader (handle, bit count, payload) for the handful of classes
    /// we care about, decoding only the fields we need and skipping the rest.
    /// See RepLayout.cpp ReceiveProperties_BackwardsCompatible in Unreal Engine.
    /// </summary>
    // Returns true with a null exportGroup for the groups we decode ourselves; callers only use the bool.
#pragma warning disable CS8765
    public override bool ReceiveProperties(FBitArchive archive, NetFieldExportGroup group, uint channelIndex,
        out INetFieldExportGroup? exportGroup, bool enablePropertyChecksum = true, bool netDeltaUpdate = false)
#pragma warning restore CS8765
    {
        if (DumpFields && _dumped.Add(group.PathName))
        {
            var names = group.NetFieldExports.Where(e => e?.Name is { } n && (n.Contains("Loc") || n.Contains("Pos") || n.Contains("Movement") || n.Contains("Squad") || n.Contains("Team")
                                                       || n.Contains("Zone") || n.Contains("Radius") || n.Contains("Center") || n.Contains("Swim") || n.Contains("Vehicle") || n.Contains("Shrink") || (n.Contains("Time") && group.PathName.Contains("GameState"))))
                                             .Select(e => e!.Name);
            var list = string.Join(",", names);
            if (list.Length > 0) Console.Error.WriteLine($"FIELDS {group.PathName}: {list}");
        }

        if (Tracing)
        {
            var tl = Channels[channelIndex]?.Actor?.Location;
            Console.Error.WriteLine($"T {_clock.FrameTime:F2} actor {Channels[channelIndex]?.ActorId} {group.PathName}{(netDeltaUpdate ? " (delta)" : "")}{(tl is { } l ? $" @ {l.X:F0},{l.Y:F0},{l.Z:F0}" : "")}");
        }

        var kind = Classify(group.PathName);
        if (netDeltaUpdate && kind != GroupKind.TeamInfo || kind == GroupKind.TeamInfo && !WantMap)
        {
            kind = GroupKind.Ignore;
        }
        if (kind == GroupKind.Ignore)
        {
            return base.ReceiveProperties(archive, group, channelIndex, out exportGroup, enablePropertyChecksum, netDeltaUpdate);
        }

        exportGroup = null;
        var channel = Channels[channelIndex];
        if (channel is null)
        {
            return false;
        }

        if (enablePropertyChecksum)
        {
            archive.ReadBit();
        }

        uint? searchingPawn = null, playerState = null, recorderPlayerState = null;
        bool? searched = null, destroyed = null, bot = null, sky = null, chute = null;
        int? kills = null;
        FVector? moved = null, teamLoc = null;
        int? mode = null;
        var zoneChanged = false;
        int? animCount = null, team = null, worldId = null, owner = null;
        bool? playerPlaced = null, building = null;
        string? platform = null;
        uint? openingPawn = null, sourceChest = null;
        bool? prizeWon = null;
        string? prize = null;

        while (true)
        {
            var handle = archive.ReadIntPacked();
            if (handle == 0)
            {
                break;
            }

            handle--;
            if (!group.IsValidIndex(handle))
            {
                return false;
            }

            var numBits = (int)archive.ReadIntPacked();
            if (numBits == 0)
            {
                continue;
            }

            if (archive.IsError || numBits > archive.GetBitsLeft())
            {
                return false;
            }

            var start = archive.GetBitsLeft();
            if (Tracing)
            {
                var posProp = archive.GetType().GetProperty("Position")!;
                var mark = posProp.GetValue(archive);
                var raw = archive.ReadBits(numBits).ToArray();
                posProp.GetSetMethod(true)!.Invoke(archive, new[] { mark });
                Console.Error.WriteLine($"F {_clock.FrameTime:F2}    .{group.NetFieldExports[handle]?.Name} ({numBits} bits) = {Convert.ToHexString(raw)}");
            }

            switch (kind, group.NetFieldExports[handle]?.Name)
            {
                case (GroupKind.Pawn, "PawnMontage" or "CurrentWeapon") when Tracing:
                {
                    var guid = archive.ReadIntPacked();
                    var path = _netGuidCache.TryGetPathName(guid, out var pn) ? pn : "?";
                    Console.Error.WriteLine($"V {_clock.FrameTime:F2}    {group.NetFieldExports[handle]?.Name} = {guid} {path}");
                    break;
                }
                case (GroupKind.Chest or GroupKind.OtherContainer, "SearchingPawn"):
                    searchingPawn = archive.ReadIntPacked();
                    break;
                case (GroupKind.Chest or GroupKind.OtherContainer, "bAlreadySearched"):
                    searched = archive.ReadBit();
                    break;
                case (GroupKind.Pawn, "bStartedInteractSearch"):
                    var on = archive.ReadBit();
                    if (channel.ActorId is uint searcher) _searchFlags.Add((_clock.FrameTime, searcher, on));
                    break;
                case (GroupKind.Pawn, "ReplicatedMovement") when WantMap:
                    moved = ReadMovement(archive, numBits);
                    break;
                case (GroupKind.Pawn, "ReplicatedMovementMode") when WantMap && numBits <= 32:
                    mode = archive.ReadBitsToInt(numBits) & 0x0F;
                    break;
                case (GroupKind.GameState, "ReplicatedWorldTimeSecondsDouble") when WantMap && numBits == 64:
                    _worldOffsets.Add(ReadReal(archive, numBits) - _clock.FrameTime);
                    break;
                case (GroupKind.SafeZone, "PreviousCenter"):
                    if (ReadVector(archive, numBits, ref _zoneVectorFormat) is { } pc) { _zone.PX = pc.X; _zone.PY = pc.Y; zoneChanged = true; }
                    break;
                case (GroupKind.SafeZone, "NextCenter"):
                    if (ReadVector(archive, numBits, ref _zoneVectorFormat) is { } nc) { _zone.NX = nc.X; _zone.NY = nc.Y; zoneChanged = true; }
                    break;
                case (GroupKind.SafeZone, "PreviousRadius") when numBits is 32 or 64:
                    _zone.PR = ReadReal(archive, numBits); zoneChanged = true;
                    break;
                case (GroupKind.SafeZone, "NextRadius") when numBits is 32 or 64:
                    _zone.NR = ReadReal(archive, numBits); zoneChanged = true;
                    break;
                case (GroupKind.SafeZone, "SafeZoneStartShrinkTime") when numBits is 32 or 64:
                    _zone.Start = ReadReal(archive, numBits); zoneChanged = true;
                    break;
                case (GroupKind.SafeZone, "SafeZoneFinishShrinkTime") when numBits is 32 or 64:
                    _zone.End = ReadReal(archive, numBits); zoneChanged = true;
                    break;
                case (GroupKind.TeamInfo, "PlayerState"):
                    playerState = archive.ReadIntPacked();
                    break;
                case (GroupKind.TeamInfo, "LastRepLocation"):
                    teamLoc = ReadVector(archive, numBits);
                    break;
                case (GroupKind.Pawn, "bIsSkydiving") when WantMap:
                    sky = archive.ReadBit();
                    break;
                case (GroupKind.Pawn, "bIsParachuteOpen") when WantMap:
                    chute = archive.ReadBit();
                    break;
                case (GroupKind.PlayerState, "KillScore") when WantMap && numBits <= 32:
                    kills = archive.ReadBitsToInt(numBits);
                    break;
                case (GroupKind.Chest, "bDestroyed"):
                    destroyed = archive.ReadBit();
                    break;
                case (GroupKind.Chest, "SearchAnimationCount") when numBits <= 32:
                    animCount = archive.ReadBitsToInt(numBits);
                    break;
                case (GroupKind.Pawn, "PlayerState"):
                case (GroupKind.ReplayController, "PlayerState"):
                    playerState = archive.ReadIntPacked();
                    break;
                case (GroupKind.GameState, "RecorderPlayerState"):
                    recorderPlayerState = archive.ReadIntPacked();
                    break;
                case (GroupKind.GameState, "GameSessionId"):
                    var session = archive.ReadFString();
                    if (!string.IsNullOrEmpty(session) && !archive.IsError) _sessionId ??= session;
                    break;
                case (GroupKind.PlayerState, "TeamIndex") when numBits <= 32:
                    team = archive.ReadBitsToInt(numBits);
                    break;
                case (GroupKind.PlayerState, "Platform"):
                    platform = archive.ReadFString();
                    break;
                case (GroupKind.PlayerState, "bIsABot"):
                    bot = archive.ReadBit();
                    break;
                case (GroupKind.PlayerState, "WorldPlayerId") when numBits <= 32:
                    worldId = archive.ReadBitsToInt(numBits);
                    break;
                case (GroupKind.Build, "OwnerPersistentID") when numBits <= 32:
                    owner = archive.ReadBitsToInt(numBits);
                    break;
                case (GroupKind.Build, "TeamIndex") when numBits <= 32:
                    team = archive.ReadBitsToInt(numBits);
                    break;
                case (GroupKind.Build, "bPlayerPlaced"):
                    playerPlaced = archive.ReadBit();
                    break;
                case (GroupKind.Treasure, "OwnerPersistentID") when numBits <= 32:
                    owner = archive.ReadBitsToInt(numBits);
                    break;
                case (GroupKind.Treasure, "OpeningPawn"):
                    openingPawn = archive.ReadIntPacked();
                    break;
                case (GroupKind.Treasure, "Instigator"):
                    openingPawn ??= archive.ReadIntPacked();
                    break;
                case (GroupKind.Treasure, "SourceContainer"):
                    sourceChest = archive.ReadIntPacked();
                    break;
                case (GroupKind.Treasure, "bSuccess"):
                    prizeWon = archive.ReadBit();
                    break;
                case (GroupKind.Treasure, "PrizeTemplateId") when numBits <= 8192:
                    prize = ReadPrizeId(archive, numBits);
                    break;
                case (GroupKind.Build, "bIsInitiallyBuilding"):
                case (GroupKind.Build, "bUnderConstruction"):
                    building = (building ?? false) | archive.ReadBit();
                    break;
            }

            var consumed = start - archive.GetBitsLeft();
            if (consumed > numBits || archive.IsError)
            {
                return false;
            }

            if (consumed < numBits)
            {
                archive.SkipBits(numBits - consumed);
            }
        }

        if (kind == GroupKind.SafeZone)
        {
            if (zoneChanged && WantMap)
            {
                _zones.Add((_clock.FrameTime, _zone.PX, _zone.PY, _zone.PR, _zone.NX, _zone.NY, _zone.NR, _zone.Start, _zone.End));
            }

            return true;
        }

        if (kind == GroupKind.Chest && WantMap && channel.ActorId is uint seenActor)
        {
            var seenId = ChestId(seenActor, channel.Actor?.Location, group.PathName);
            if (!seenId.StartsWith('#') && !seenId.Contains('@')) _seenChests.Add(seenId);
        }

        if (kind == GroupKind.TeamInfo)
        {
            exportGroup = new TeamMemberUpdate { PlayerState = playerState, Location = teamLoc };
            return true;
        }

        switch (kind)
        {
            case GroupKind.Chest when channel.ActorId is uint actor
                                      && (searchingPawn != null || searched != null || destroyed != null || animCount != null):
                var id = ChestId(actor, channel.Actor?.Location, group.PathName);
                if (WantMap && channel.Actor?.Location is { } spawn && !_chestSpawn.ContainsKey(id))
                {
                    _chestSpawn[id] = ((float)spawn.X, (float)spawn.Y);
                }

                _updates.Add(new ChestUpdate(_clock.FrameTime, id, ClassName(group.PathName), searchingPawn, searched, destroyed, animCount));
                break;
            case GroupKind.OtherContainer when searchingPawn is > 0 && searched == true:
                _otherSearches.Add((_clock.FrameTime, searchingPawn.Value, ClassName(group.PathName)));
                break;
            case GroupKind.Pawn when channel.ActorId is uint pawn:
                if (playerState is > 0)
                {
                    _pawnToPlayerState[pawn] = playerState.Value;
                }

                if (moved is not null)
                {
                    if (!_moves.TryGetValue(pawn, out var path)) _moves[pawn] = path = new();
                    if (path.Count == 0 || _clock.FrameTime - path[^1].T >= MoveStep)
                    {
                        path.Add((_clock.FrameTime, (float)moved.X, (float)moved.Y, (float)moved.Z));
                    }
                }

                if (mode is not null)
                {
                    _modes.Add((_clock.FrameTime, pawn, mode.Value));
                }

                if (sky is not null || chute is not null)
                {
                    _airFlags.Add((_clock.FrameTime, pawn, sky, chute));
                }

                break;
            case GroupKind.ReplayController when playerState is > 0:
                _controllerPlayerState ??= playerState;
                break;
            case GroupKind.GameState when recorderPlayerState is > 0:
                _recorderPlayerState ??= recorderPlayerState;
                break;
            case GroupKind.Treasure when channel.ActorId is uint gold:
                if (!_treasures.TryGetValue(gold, out var tr))
                {
                    _treasures[gold] = tr = new TreasureInfo { FirstSeen = _clock.FrameTime };
                }

                tr.Owner ??= owner;
                tr.OpeningPawn ??= openingPawn is > 0 ? openingPawn : null;
                tr.Won ??= prizeWon;
                tr.Prize ??= prize;
                tr.Source ??= sourceChest is > 0 ? sourceChest : null;
                break;
            case GroupKind.Build when channel.ActorId is uint piece && (playerPlaced == true || _builds.ContainsKey(piece)):
                if (!_builds.TryGetValue(piece, out var b))
                {
                    _builds[piece] = b = new BuildInfo { FirstSeen = _clock.FrameTime, JustPlaced = building == true };
                }

                b.Owner ??= owner;
                b.Team ??= team;
                break;
            case GroupKind.PlayerState when channel.ActorId is uint ps:
                if (kills is not null) _killScores.Add((_clock.FrameTime, ps, kills.Value));
                var info = Player(ps);
                if (worldId is not null) info.WorldId = worldId;
                if (team is not null) info.Team = team;
                if (!string.IsNullOrEmpty(platform)) info.Platform = platform;
                if (bot is not null) info.Bot = bot.Value;
                break;
        }

        return true;
    }

    /// <summary>Quantization variants of FRepMovement to try (location, rotation, velocity); the one that
    /// uses exactly the field's bits and gives a position on the island is kept for the rest of the replay.</summary>
    private static readonly (VectorQuantization, RotatorQuantization, VectorQuantization)[] MoveFormats =
    {
        (VectorQuantization.RoundTwoDecimals, RotatorQuantization.ShortComponents, VectorQuantization.RoundWholeNumber),
        (VectorQuantization.RoundWholeNumber, RotatorQuantization.ShortComponents, VectorQuantization.RoundWholeNumber),
        (VectorQuantization.RoundTwoDecimals, RotatorQuantization.ByteComponents, VectorQuantization.RoundWholeNumber),
        (VectorQuantization.RoundWholeNumber, RotatorQuantization.ByteComponents, VectorQuantization.RoundWholeNumber),
        (VectorQuantization.RoundOneDecimal, RotatorQuantization.ShortComponents, VectorQuantization.RoundWholeNumber),
        (VectorQuantization.RoundTwoDecimals, RotatorQuantization.ShortComponents, VectorQuantization.RoundTwoDecimals),
    };

    private int _vectorFormat = -1;

    /// <summary>Decodes a vector field whose exact type isn't known: plain floats, or one of the quantized
    /// forms. The one that uses exactly the field's bits and lands on the island is kept.</summary>
    private FVector? ReadVector(FBitArchive archive, int numBits) => ReadVector(archive, numBits, ref _vectorFormat);

    /// <summary>A float or double field (32 or 64 bits).</summary>
    private static double ReadReal(FBitArchive archive, int numBits)
    {
        var bytes = archive.ReadBits(numBits).ToArray();
        return numBits == 64 ? BitConverter.ToDouble(bytes, 0) : BitConverter.ToSingle(bytes, 0);
    }

    private FVector? ReadVector(FBitArchive archive, int numBits, ref int formatSlot)
    {
        var bytes = archive.ReadBits(numBits).ToArray();
        Func<NetBitReader, FVector>[] formats =
        {
            r => r.SerializePropertyVector100(),
            r => r.SerializePropertyQuantizedVector(VectorQuantization.RoundWholeNumber),
            r => r.SerializePropertyVector10(),
            r => r.SerializePropertyVector(),
            r => r.SerializePropertyQuantizedVector(VectorQuantization.RoundTwoDecimals),
        };
        var order = formatSlot >= 0 ? new[] { formatSlot } : Enumerable.Range(0, formats.Length).ToArray();
        foreach (var i in order)
        {
            try
            {
                var r = new NetBitReader(bytes, numBits)
                {
                    EngineNetworkVersion = archive.EngineNetworkVersion,
                    NetworkVersion = archive.NetworkVersion,
                    ReplayHeaderFlags = archive.ReplayHeaderFlags,
                };
                var v = formats[i](r);
                if (!r.IsError && r.GetBitsLeft() == 0 && v is not null
                    && Math.Abs(v.X) < 400_000 && Math.Abs(v.Y) < 400_000 && Math.Abs(v.Z) < 300_000)
                {
                    if (formatSlot < 0 && DumpFields) Console.Error.WriteLine($"vector format {i} ({numBits} bits)");
                    formatSlot = i;
                    return v;
                }
            }
            catch (Exception)
            {
                // try the next format
            }
        }

        MoveDecodeFailures++;
        return null;
    }

    /// <summary>Decodes a pawn's ReplicatedMovement (FRepMovement) and returns its location, or null.</summary>
    private FVector? ReadMovement(FBitArchive archive, int numBits)
    {
        var bytes = archive.ReadBits(numBits).ToArray();
        var order = _moveFormat >= 0 ? new[] { _moveFormat } : Enumerable.Range(0, MoveFormats.Length).ToArray();
        foreach (var i in order)
        {
            try
            {
                var r = new NetBitReader(bytes, numBits)
                {
                    EngineNetworkVersion = archive.EngineNetworkVersion,
                    NetworkVersion = archive.NetworkVersion,
                    ReplayHeaderFlags = archive.ReplayHeaderFlags,
                };
                var (loc, rot, vel) = MoveFormats[i];
                var m = r.SerializeRepMovement(loc, rot, vel);
                if (!r.IsError && r.GetBitsLeft() == 0 && m.Location is { } p
                    && Math.Abs(p.X) < 400_000 && Math.Abs(p.Y) < 400_000 && Math.Abs(p.Z) < 300_000)
                {
                    _moveFormat = i;
                    return p;
                }
            }
            catch (Exception)
            {
                // try the next variant
            }
        }

        MoveDecodeFailures++;
        return null;
    }

    /// <summary>Real Fortnite classes have at most a few thousand replicated fields.</summary>
    private const uint MaxExportsPerGroup = 1 << 16;

    /// <summary>Corrupt export blocks skipped without allocating (diagnostics).</summary>
    public int RejectedCorruptBlocks { get; private set; }

    /// <summary>
    /// A few replays contain a corrupt export block whose string length reads as ~1 billion characters.
    /// Unreal.Core allocates a buffer that size (up to 2 GB) before the read fails and the packet is
    /// dropped. Walk the block first, checking every length against the data actually left; if one is
    /// impossible, drop the packet the same way (by throwing), just without the giant allocation.
    /// </summary>
    public override void ReceiveNetFieldExportsCompat(FBitArchive bitArchive)
    {
        bitArchive.Mark();
        var impossible = HasImpossibleLength(bitArchive);
        bitArchive.Pop();

        if (impossible)
        {
            RejectedCorruptBlocks++;
            throw new InvalidDataException("Corrupt net field export block (impossible length).");
        }

        base.ReceiveNetFieldExportsCompat(bitArchive);
    }

    /// <summary>
    /// Mirrors the layout read by ReceiveNetFieldExportsCompat / ReadNetFieldExport. Only reports true
    /// for a provably impossible length; if the data merely runs out it stops and lets the original
    /// code handle it. Never reads past the end, so it can't set the archive's error flag.
    /// </summary>
    private static bool HasImpossibleLength(FBitArchive a)
    {
        var version = a.EngineNetworkVersion;
        if (a.GetBitsLeft() < 32) return false;
        var count = a.ReadUInt32();

        for (var i = 0; i < count; i++)
        {
            if (a.GetBitsLeft() < 48) return false; // path index (packed) + flag bit
            a.ReadIntPacked();
            if (a.ReadBit())
            {
                if (SkipFString(a) is not { } ok) return false;
                if (!ok) return true;
                if (a.GetBitsLeft() < 32) return false;
                if (a.ReadUInt32() > MaxExportsPerGroup) return true;
            }

            // ReadNetFieldExport
            if (a.GetBitsLeft() < 1) return false;
            if (!a.ReadBit()) continue;
            if (a.GetBitsLeft() < 72) return false; // handle (packed) + checksum
            a.ReadIntPacked();
            a.ReadUInt32();

            if (version < EngineNetworkVersionHistory.HISTORY_NETEXPORT_SERIALIZE_FIX)
            {
                var strings = version < EngineNetworkVersionHistory.HISTORY_NETEXPORT_SERIALIZATION ? 2 : 1;
                for (var s = 0; s < strings; s++)
                {
                    if (SkipFString(a) is not { } ok) return false;
                    if (!ok) return true;
                }
            }
            else
            {
                // FName: hardcoded index, or string + number
                if (a.GetBitsLeft() < 1) return false;
                if (a.ReadBit())
                {
                    if (a.GetBitsLeft() < 40) return false;
                    if (version < EngineNetworkVersionHistory.HISTORY_CHANNEL_NAMES) a.ReadUInt32(); else a.ReadIntPacked();
                }
                else
                {
                    if (SkipFString(a) is not { } ok) return false;
                    if (!ok) return true;
                    if (a.GetBitsLeft() < 32) return false;
                    a.ReadInt32();
                }
            }
        }

        return false;
    }

    /// <summary>true = skipped a valid string, false = impossible length, null = ran out of data.</summary>
    private static bool? SkipFString(FBitArchive a)
    {
        if (a.GetBitsLeft() < 32) return null;
        long length = a.ReadInt32();
        var bits = (length < 0 ? -2 * length : length) * 8;
        if (bits > a.GetBitsLeft()) return false;
        a.SkipBits((int)bits);
        return true;
    }

    /// <summary>
    /// Unreal.Core marks the end of each chunk with BitReader.SetTempEnd(size, slot), which saves the
    /// previous end in that slot, and undoes it with RestoreTempEnd(slot) in a finally block. When a
    /// chunk claims to be larger than the data left, SetTempEnd fails without saving anything, and the
    /// restore then uses whatever an earlier packet left in the slot. That can move the read position
    /// backwards, so the packet loop re-reads the same bad chunk forever. One replay did exactly that.
    ///
    /// Fix: right before the parser opens such a chunk, store the current (correct) end in the slot.
    /// A failed SetTempEnd then "restores" to the end of the enclosing chunk, so reading moves on.
    /// </summary>
    private static void PrimeTempEnd(FBitArchive archive, FBitArchiveEndIndex slot)
    {
        if (archive.IsError)
        {
            return; // RestoreTempEnd would clear the error flag; leave an errored archive alone.
        }

        archive.SetTempEnd(0, slot);  // saves the current end in the slot; end = current position
        archive.RestoreTempEnd(slot); // position unchanged, end back to what it was
    }

    public override void ProcessBunch(DataBunch bunch)
    {
        PrimeTempEnd(bunch.Archive, FBitArchiveEndIndex.CONTENT_BLOCK_PAYLOAD);
        base.ProcessBunch(bunch);
    }

    public override bool ReceivedReplicatorBunch(DataBunch bunch, FBitArchive archive, uint? repObject, bool bHasRepLayout)
    {
        PrimeTempEnd(archive, FBitArchiveEndIndex.FIELD_HEADER_PAYLOAD);
        _bunchChannel = bunch.ChIndex;
        return base.ReceivedReplicatorBunch(bunch, archive, repObject, bHasRepLayout);
    }

    /// <summary>Channel of the bunch being read (ReadFieldHeaderAndPayload doesn't get it).</summary>
    private uint _bunchChannel;

    /// <summary>
    /// Squad members' positions are in the team's private info, in two fast arrays ("RepData" and
    /// "LatentTeamRepData", custom delta properties). Unreal.Core skips custom delta properties of
    /// classes it has no C# type for, so read a copy of the payload here with its own fast array
    /// reader; each element then arrives in ReceiveProperties (netDeltaUpdate) and OnNetDeltaRead.
    /// The original payload is left in place for Unreal.Core to skip as usual.
    /// </summary>
#pragma warning disable CS8765, CS8601
    public override bool ReadFieldHeaderAndPayload(FBitArchive archive, NetFieldExportGroup group, out NetFieldExport outField, out uint? payload)
#pragma warning restore CS8765
    {
        var ok = base.ReadFieldHeaderAndPayload(archive, group, out outField, out payload);
#pragma warning restore CS8601
        // Treasure prizes: each one grants the player a "GA_TreasureHandler_*" ability (see ReadAbilityGrants)
        if (ok && payload is uint gbits && gbits > 128 && gbits <= archive.GetBitsLeft()
            && group.PathName.Contains("AbilitySystemComponent", StringComparison.Ordinal)
            && group.PathName.EndsWith("_ClassNetCache", StringComparison.Ordinal)
            && outField?.Name is null or "" or "?" or "ActivatableAbilities")
        {
            archive.Mark();
            var gbytes = archive.ReadBits((int)gbits).ToArray();
            archive.Pop();
            ReadAbilityGrants(_bunchChannel, gbytes, (int)gbits);
        }
        if (ok && WantMap && payload is uint bits && bits > 0 && bits <= archive.GetBitsLeft()
            && outField?.Name is "RepData" or "LatentTeamRepData"
            && group.PathName.EndsWith("FortTeamPrivateInfo_ClassNetCache", StringComparison.Ordinal)
            && _netGuidCache.GetNetFieldExportGroup("/Script/FortniteGame.FortTeamPrivateInfo") is { } members)
        {
            archive.Mark();
            var bytes = archive.ReadBits((int)bits).ToArray();
            archive.Pop();
            try
            {
                var copy = new NetBitReader(bytes, (int)bits)
                {
                    EngineNetworkVersion = archive.EngineNetworkVersion,
                    NetworkVersion = archive.NetworkVersion,
                    ReplayHeaderFlags = archive.ReplayHeaderFlags,
                };
                ReceiveCustomDeltaProperty(copy, members, _bunchChannel, enablePropertyChecksum: true);
            }
            catch (Exception ex)
            {
                if (DumpFields) Console.Error.WriteLine($"team delta failed: {ex.Message}");
            }
        }

        return ok;
    }

    protected override void OnExportRead(uint channelIndex, INetFieldExportGroup? exportGroup)
    {
    }

    /// <summary>
    /// Player names arrive as "external data" on the player's PlayerState, lightly scrambled (each
    /// character shifted by a small amount). Same decoding as FortniteReplayDecompressor's PlayerNameData.
    /// </summary>
    protected override void OnExternalDataRead(uint channelIndex, IExternalData? exportGroup)
    {
        if (exportGroup?.Archive is not { } a)
        {
            return;
        }

        try
        {
            a.ReadByte();                   // handle
            a.ReadByte();                   // unknown
            var isPlayer = a.ReadBoolean();
            var encoded = a.ReadFString();
            if (string.IsNullOrEmpty(encoded) || a.IsError)
            {
                return;
            }

            var name = encoded;
            if (isPlayer)
            {
                var chars = new char[encoded.Length];
                for (var i = 0; i < encoded.Length; i++)
                {
                    var shift = (encoded.Length % 4 * 3 % 8 + 1 + i) * 3 % 8;
                    chars[i] = (char)(encoded[i] + shift);
                }

                name = new string(chars);
            }

            Player(exportGroup.NetGUID).Name = name;
        }
        catch (Exception)
        {
            // not a name block; ignore
        }
    }

    /// <summary>One squad member's entry in the team's private info (what the minimap's teammate markers use).
    /// It is replicated to the whole squad all match long, unlike far-away pawns' own movement.</summary>
    private sealed class TeamMemberUpdate : INetFieldExportGroup
    {
        public uint? PlayerState;
        public FVector? Location;
    }

    private readonly Dictionary<(uint, int), uint> _teamSlots = new();

    /// <summary>Squad members' positions from the team info: (time, player state, x, y, z).</summary>
    private readonly List<(float T, uint Ps, float X, float Y, float Z)> _teamLocs = new();

    protected override void OnNetDeltaRead(uint channelIndex, NetDeltaUpdate update)
    {
        if (update.Export is not TeamMemberUpdate tm)
        {
            return;
        }

        var slot = (channelIndex, update.ElementIndex);  // RepData and LatentTeamRepData number their members alike
        if (tm.PlayerState is > 0)
        {
            _teamSlots[slot] = tm.PlayerState.Value;
        }

        if (tm.Location is { } loc && _teamSlots.TryGetValue(slot, out var ps))
        {
            _teamLocs.Add((_clock.FrameTime, ps, (float)loc.X, (float)loc.Y, (float)loc.Z));
        }
    }

    /// <summary>Events (kill feed, match stats, ...) aren't needed; skip them.</summary>
    public override void ReadEvent(FArchive archive)
    {
        archive.ReadFString(); // id
        archive.ReadFString(); // group
        archive.ReadFString(); // metadata
        archive.ReadUInt32();  // start time
        archive.ReadUInt32();  // end time
        archive.SkipBytes(archive.ReadInt32());
    }

    protected override FArchive DecryptBuffer(FArchive archive, int size)
    {
        if (!Replay.Info.IsEncrypted)
        {
            return new Unreal.Core.BinaryReader(archive.ReadMemory(size))
            {
                EngineNetworkVersion = Replay.Header.EngineNetworkVersion,
                NetworkVersion = Replay.Header.NetworkVersion,
                ReplayHeaderFlags = Replay.Header.Flags,
                ReplayVersion = Replay.Info.FileVersion,
            };
        }

        using var aes = Aes.Create();
        aes.Key = Replay.Info.EncryptionKey.ToArray();
        var decrypted = aes.DecryptEcb(archive.ReadMemory(size).Span, PaddingMode.PKCS7);

        return new Unreal.Core.BinaryReader(decrypted.AsMemory())
        {
            EngineNetworkVersion = archive.EngineNetworkVersion,
            NetworkVersion = archive.NetworkVersion,
            ReplayHeaderFlags = archive.ReplayHeaderFlags,
            ReplayVersion = archive.ReplayVersion,
        };
    }

    protected override FArchive Decompress(FArchive archive)
    {
        if (!Replay.Info.IsCompressed)
        {
            return archive;
        }

        var decompressedSize = archive.ReadInt32();
        var compressedSize = archive.ReadInt32();
        var compressed = archive.ReadBytes(compressedSize);
        var output = Unreal.Encryption.Oodle.DecompressReplayData(compressed, decompressedSize);

        return new Unreal.Core.BinaryReader(output)
        {
            EngineNetworkVersion = archive.EngineNetworkVersion,
            NetworkVersion = archive.NetworkVersion,
            ReplayHeaderFlags = archive.ReplayHeaderFlags,
            ReplayVersion = archive.ReplayVersion,
        };
    }

    /// <summary>
    /// Unreal.Core doesn't expose the current demo-frame time, but it logs it at Debug level
    /// ("ReadDemoFrameIntoPlaybackPackets at {time}"). Capture that; everything else is dropped.
    /// Only used to timestamp chest opens, so it degrades gracefully if the message ever changes.
    /// </summary>
    private sealed class FrameClock : ILogger
    {
        public float FrameTime;

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => logLevel == LogLevel.Debug;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
        {
            if (logLevel == LogLevel.Debug
                && state is IReadOnlyList<KeyValuePair<string, object?>> { Count: 2 } values
                && values[0].Value is float time
                && values[1].Value is string format
                && format.StartsWith("ReadDemoFrameIntoPlaybackPackets", StringComparison.Ordinal))
            {
                FrameTime = time;
            }
        }
    }
}
