// ChestReplayReader: reads finished Fortnite .replay files and prints, for each one, a single
// line of JSON listing the chests the recording player opened or broke.
//
//   ChestReplayReader <file.replay> [more files...]
//   ChestReplayReader -v <file.replay>     also print a readable list to stderr
//   ChestReplayReader --map <file.replay>  also routes, landing/kill spots and chest positions ("map")
//   ChestReplayReader --squad <file.replay> also where the recorder's own squad went ("squad"): routes only,
//                                           nothing about other teams (safe to show during a match)
//
// Used by chest_tracker.py. It only reads files from disk; it never touches the running game.
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using ChestReplayReader;

// CT_BACKGROUND=1 (the tracker sets it while a match is being played): stay out of the game's way. Lowest CPU
// priority, Windows background mode (low disk and memory priority too) and a single CPU core.
if (Environment.GetEnvironmentVariable("CT_BACKGROUND") == "1" && OperatingSystem.IsWindows())
{
    try
    {
        Background.SetPriorityClass(Background.GetCurrentProcess(), 0x00100000);  // PROCESS_MODE_BACKGROUND_BEGIN
        var me = Process.GetCurrentProcess();
        me.PriorityClass = ProcessPriorityClass.Idle;  // (after background mode, which resets it)
        var cores = Environment.ProcessorCount;
        if (cores > 1)
        {
            me.ProcessorAffinity = (IntPtr)(1L << (cores - 1));  // the last core (games lean on the first ones)
        }
    }
    catch (Exception)
    {
        // not allowed here: read at the normal (below normal) priority
    }
}

var verbose = args.Contains("-v");
var withMap = args.Contains("--map");  // also routes and chest positions, for the map window (slower)
var liveOk = args.Contains("--live");  // read a replay that's still recording (mid-match), if its key is there
var squadOnly = args.Contains("--squad");  // the recorder's squad's routes only (for the live map)
var files = args.Where(a => a != "-v" && a != "--map" && a != "--live" && a != "--squad").ToList();
if (files.Count == 0)
{
    Console.Error.WriteLine("usage: ChestReplayReader [-v] <file.replay>...");
    return 2;
}

var json = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
foreach (var file in files)
{
    object line;
    try
    {
        var (encrypted, keyLength) = ReplayFile.LiveKeyLength(file);
        if (ReplayFile.IsStillRecording(file) && (!liveOk || encrypted && keyLength <= 0))
        {
            // Without --live, or when Fortnite hasn't written the decryption key yet: nothing to read.
            line = new { file, live = true, complete = false, error = liveOk ? "no key yet" : "still recording",
                         encrypted, key_length = keyLength };
        }
        else
        {
            // --squad decodes movement like --map, but only the squad's routes are printed (see below)
            var reader = new ChestReader { WantMap = withMap || squadOnly, AllowLive = liveOk };
            var r = reader.Read(file);
            line = new
            {
                file,
                guid = r.ReplayGuid,
                start_utc = r.MatchStartUtc?.ToString("o"),
                length_ms = r.LengthMs,
                branch = r.Branch,
                live = r.IsLive,
                complete = r.Complete,
                error = r.Error,
                recorder_found = r.RecorderFound,
                opened = r.MyChests.Count(c => !c.Broken),
                broken = r.MyChests.Count(c => c.Broken),
                build_breaks = r.MyChests.Count(c => c.ByBuild),
                opened_by_anyone = r.ChestsOpenedByAnyone,
                session_id = r.SessionId,
                recorder_name = r.RecorderName,
                recorder_platform = r.RecorderPlatform,
                recorder_searches = r.RecorderSearches,
                read_upto = MathF.Round(r.ReadUpTo, 1),
                treasure_v = 3,  // 2: sprite gizmo prizes (prize grants); 3: credited to the chest's opener
                treasures = r.Treasures.Select(t => new { t = MathF.Round(t.Time, 1), prize = t.Prize, won = t.Won,
                                                          name = t.Name, platform = t.Platform, who = t.Who, from = t.From }),
                teammates = r.Teammates.Select(t => new { name = t.Name, platform = t.Platform, bot = t.Bot,
                                                          opened = t.Opened, broken = t.Broken, build_breaks = t.BrokenByBuild,
                                                          searches = t.Searches, seen_opened = t.SeenOpened }),
                map = withMap ? r.Map : null,
                // Fair play: only the recorder's own team (MapData.Players is filtered by TeamIndex in
                // ChestReader.ResolveMap), only positions: no kills, chests, storm or other teams.
                squad = squadOnly && r.Map is { } m
                    ? m.Players.Select(p => new { name = p.Name, recorder = p.Recorder, route = SquadRoute(p.Route) })
                    : null,
                move_failures = withMap ? reader.MoveDecodeFailures : (int?)null,
                chests = r.MyChests.Select(c => new { t = MathF.Round(c.Time, 1), cls = c.ChestClass, id = c.ChestId, broken = c.Broken, by_build = c.ByBuild }),
            };
            if (verbose)
            {
                Console.Error.WriteLine($"{Path.GetFileName(file)}: {r.MyChests.Count(c => !c.Broken)} opened, {r.MyChests.Count(c => c.Broken)} broken ({r.MyChests.Count(c => c.ByBuild)} with builds)"
                                        + (r.RecorderFound ? "" : " (recorder not found)") + (r.Complete ? "" : $" (partial: {r.Error})"));
                foreach (var c in r.MyChests)
                {
                    Console.Error.WriteLine($"   {TimeSpan.FromSeconds(c.Time):mm\\:ss}  {(c.ByBuild ? "BUILD" : c.Broken ? "BROKEN" : "opened"),-6}  {c.ChestClass,-34} {c.ChestId}");
                }

                Console.Error.WriteLine($"   recorder: {r.RecorderName} ({r.RecorderPlatform}), {r.RecorderSearches} searches");
                foreach (var t in r.Treasures)
                    Console.Error.WriteLine($"   TREASURE {TimeSpan.FromSeconds(t.Time):mm\\:ss}  {t.Prize}  opened by {t.Name} ({t.Who}){(t.Won ? "" : ", not won")}");
                foreach (var t in r.Teammates)
                {
                    Console.Error.WriteLine($"   teammate: {t.Name} ({t.Platform}{(t.Bot ? ", bot" : "")}): {t.Opened} opened, {t.Broken} broken ({t.BrokenByBuild} with builds)"
                                            + $" ({t.Searches} searches, {t.SeenOpened} chests seen opened)");
                }
            }
        }
    }
    catch (Exception ex)
    {
        line = new { file, complete = false, error = ex.Message };
    }

    Console.WriteLine(JsonSerializer.Serialize(line, json));
}

return 0;

/// <summary>A squad member's route thinned to a point every 3 s: [t, x, y].</summary>
static List<float[]> SquadRoute(List<float[]> route)
{
    var outp = new List<float[]>();
    foreach (var pt in route)
    {
        if (outp.Count == 0 || pt[0] - outp[^1][0] >= 3f) outp.Add(new[] { pt[0], pt[1], pt[2] });
    }

    if (route.Count > 0 && (outp.Count == 0 || outp[^1][0] != route[^1][0])) outp.Add(new[] { route[^1][0], route[^1][1], route[^1][2] });
    return outp;
}

internal static class Background
{
    [DllImport("kernel32.dll")]
    internal static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool SetPriorityClass(IntPtr process, uint priorityClass);
}
