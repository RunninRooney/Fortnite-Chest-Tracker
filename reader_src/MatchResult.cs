// Adapted from rossalv/fortnite-chest-counter (MIT, see LICENSE-rossalv.txt).
namespace ChestReplayReader;

/// <summary>A chest the replay's recording player opened or broke.</summary>
/// <param name="Time">Seconds since the replay started recording.</param>
/// <param name="ChestClass">Blueprint class, e.g. "Tiered_Chest_Athena" or "AlwaysSpawn_RareChest".</param>
/// <param name="ChestId">Stable actor name of the chest within the match.</param>
/// <param name="Broken">True if it was destroyed (pickaxe, explosives...) instead of opened.</param>
/// <param name="ByBuild">Broken by placing a build on it (a cone over it, a wall through it...).</param>
/// <summary>A treasure hunt prize: a gold chest someone opened.</summary>
/// <param name="Prize">Prize id, e.g. "DelilahPrize:dpid_item21" (a bare GUID for Pirate Shop access).</param>
/// <param name="Who">"me" (the recorder), "teammate" or "other".</param>
/// <param name="From">The chest it came out of (its actor name, e.g. "Tiered_Chest_Athena_C_UAID_...").</param>
public sealed record TreasureFind(float Time, string? Prize, bool Won, string? Name, string? Platform, string Who,
                                  string? From = null);

public sealed record ChestOpen(float Time, string ChestClass, string ChestId, bool Broken, bool ByBuild = false);

/// <summary>A player on the recorder's team and their chests (see ChestReader.ResolveTeammates).</summary>
/// <param name="Opened">Best count of chests they opened: from chests seen opened or their completed searches.</param>
/// <param name="Broken">Chests they broke near the recorder (by hand or with a build).</param>
/// <param name="Searches">Searches they ran to the end, anywhere on the map.</param>
/// <param name="SeenOpened">Chests seen being opened by them near the recorder.</param>
/// <param name="BrokenByBuild">Of those, broken by placing a build on them.</param>
public sealed record Teammate(string? Name, string? Platform, bool Bot, int Opened, int Broken, int Searches, int SeenOpened,
                              int BrokenByBuild = 0);

/// <summary>Result of scanning one .replay file.</summary>
public sealed class MatchResult
{
    public string FilePath { get; set; } = "";
    public long FileSize { get; set; }
    public DateTime LastWriteUtc { get; set; }

    /// <summary>Unique id of the recording (survives the file being renamed or copied).</summary>
    public string? ReplayGuid { get; set; }
    public string? FriendlyName { get; set; }
    public DateTime? MatchStartUtc { get; set; }
    public uint LengthMs { get; set; }
    public string? Branch { get; set; }

    /// <summary>True while Fortnite is still recording this replay.</summary>
    public bool IsLive { get; set; }

    /// <summary>False if parsing stopped early (file still being written, or unsupported data).</summary>
    public bool Complete { get; set; }
    public string? Error { get; set; }

    /// <summary>True if the recording player's pawn was found (otherwise the count can't be attributed).</summary>
    public bool RecorderFound { get; set; }

    public List<ChestOpen> MyChests { get; set; } = new();

    /// <summary>Treasure hunt gold chests seen in this replay (see ChestReader.ResolveTreasures).</summary>
    public List<TreasureFind> Treasures { get; set; } = new();

    /// <summary>Chests seen being opened by anyone (you, teammates, enemies) while in view.</summary>
    public int ChestsOpenedByAnyone { get; set; }

    /// <summary>The recording player's in-game name and platform (e.g. "WIN", "PSN").</summary>
    public string? RecorderName { get; set; }

    /// <summary>The match id (same in every player's replay of the match), if the replay has it.</summary>
    public string? SessionId { get; set; }
    public string? RecorderPlatform { get; set; }

    /// <summary>Searches the recorder ran to the end (chests, ammo boxes, ...): a cross-check.</summary>
    public int RecorderSearches { get; set; }

    /// <summary>Seconds of match time read: the end of the data Fortnite had written (mid-match) or the end.</summary>
    public float ReadUpTo { get; set; }

    /// <summary>The recorder's teammates with the chests they opened/broke near the recorder.</summary>
    public List<Teammate> Teammates { get; set; } = new();

    /// <summary>Routes and chest positions (only with --map).</summary>
    public MapData? Map { get; set; }
}

/// <summary>Where things happened in the match, for the map window (only filled with --map).
/// Positions are Unreal world units (cm); times are seconds since the replay started.</summary>
public sealed class MapData
{
    /// <summary>The recorder first, then each teammate.</summary>
    public List<MapPlayer> Players { get; set; } = new();

    /// <summary>Chests the squad opened or broke, with where they were.</summary>
    public List<MapChest> Chests { get; set; } = new();

    /// <summary>Every map-placed chest seen opened by anyone, placed where its opener stood: seeds the
    /// chest-spot database (map-placed chests keep the same actor name in every match).</summary>
    public List<MapSpot> Spots { get; set; } = new();

    /// <summary>Debug (CT_SEARCH_LOG=1): every finished search with its length and what it turned out to be.</summary>
    public List<MapSearch>? Searches { get; set; }

    /// <summary>Storm phases: the circle shrinks from (PX, PY, PR) to (NX, NY, NR) between Start and End
    /// (seconds since the replay started).</summary>
    public List<MapStorm> Storm { get; set; } = new();

    /// <summary>Map-placed chests that were there in this match (seen in the recording, opened or not).</summary>
    public List<string> Seen { get; set; } = new();
}

public sealed record MapSearch(string Who, float T, float D, string Kind, float X, float Y);

public sealed record MapStorm(float T, float PX, float PY, float PR, float NX, float NY, float NR, float Start, float End);

/// <param name="Route">[t, x, y, z, mode] about once a second, from the player's replicated movement. mode: Unreal's
/// movement mode (1 walking, 3 falling, 4 swimming, 5 flying, 6 custom), 0 if unknown.</param>
/// <param name="Landing">[t, x, y] where they first touched the ground after the bus.</param>
/// <param name="Kills">Each time their elimination count went up: where they stood, and where the player
/// they eliminated was last seen (when that can be told).</param>
/// <param name="End">[t, x, y] their last known position (eliminated, or the end of the replay).</param>
public sealed record MapPlayer(string? Name, bool Recorder, List<float[]> Route, float[]? Landing, List<MapKill> Kills, float[]? End);

/// <param name="X">Where the eliminating player stood.</param>
/// <param name="VX">Where the eliminated player was last seen (null if not known).</param>
/// <param name="Approx">The count went up by several at once (a far-away teammate's count arrives in
/// batches): time and place are only roughly right.</param>
public sealed record MapKill(float T, float X, float Y, float? VX, float? VY, string? Victim, bool Approx = false);

/// <param name="Who">Name of the squad member who opened/broke it.</param>
/// <param name="Source">"actor": the chest's own spawn position; "opener": where the opener stood;
/// "search": a teammate's full search far from the recorder (very likely a chest, not seen directly).</param>
public sealed record MapChest(float T, string? Who, string? Id, string? Class, bool Broken, bool ByBuild, float X, float Y, string Source);

/// <param name="Id">The chest's actor name (stable across matches for map-placed chests).</param>
public sealed record MapSpot(string Id, string Class, float X, float Y, string Source);
