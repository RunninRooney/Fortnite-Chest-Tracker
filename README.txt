CHEST TRACKER
=============
Counts the Fortnite chests you open AND break, automatically, and adds up your whole
team's chests live in a small overlay:

   [chest]  7 ✓        TEAM GAME   17    <- everyone on your team, this game
                       MY TOTAL  2192    <- every chest you've had this season
                       TEAM TOTAL 5410   <- everyone on your team, all season
   ^ chests YOU got this game (✓ = confirmed from the match replay)

Click the overlay to open a list of every player (this game and total). Click
anywhere else to close it.

SETUP (each person does this on their own PC)
1. Unzip this folder somewhere you'll keep it, like Documents.
2. Double-click "Start Chest Tracker.bat".
   The first time, it installs what it needs (5-10 minutes): Python, and Microsoft's .NET SDK,
   which it uses to build the replay reader on your PC (that's what counts BROKEN chests).
   .NET goes into your own Windows user folder, so no admin prompt. After that it starts
   right away. If broken chests never show up, double-click setup_reader.bat to see why.
   (The zip has no programs in it, only text files, so browsers don't block the download.
   If Windows says "Windows protected your PC" about the .bat, click More info > Run anyway.)
3. A setup window asks three quick things:
   - your name (what your squad sees) and your Fortnite name (optional: it's found from your replays)
   - how you'll play: with your squad (everyone types the same TEAM CODE; "Make a code" makes a strong
     one), just you, or private (offline mode: nothing leaves your PC)
   - it checks Fortnite's settings (replays on, Windowed Fullscreen) and says what to change.
   Capitals and spaces in the code don't matter.
4. If Windows Firewall asks about Python, click Allow.
6. In Fortnite:
   - Settings > Display > Window Mode > "Windowed Fullscreen".
     Regular Fullscreen hides every overlay, including this one.
   - Settings > Game > Replays > Record Replays: ON. This is what lets the tracker
     count chests you BREAK and double-check every match.

The first time it runs, it reads the replays already on your PC from this season
(Fortnite keeps your last 100 matches) and adds them to your total. This takes a
few minutes, and the overlay shows the progress.

UPDATES AND PROBLEM REPORTS
- Every time you run "Start Chest Tracker.bat" it checks for a newer version and installs it.
  No more downloading zips. Updates are signed: only ones from the publisher are installed.
- If something goes wrong, the black window stays open and shows what happened.
- Problem reports are off unless you turn them on (Settings > Privacy). When on, each start sends
  a short report (what happened, errors, the tracker's log, with your Windows user name and folder
  paths blanked out), sealed so only the publisher can read it.

PRIVACY AND OFFLINE MODE
- Settings > Privacy > Offline mode: nothing leaves your PC and nothing is downloaded. No squad
  sync, no update checks, no problem reports, no map download. Counting works the same.
- Other players' names (random squad fills, opponents) never leave your PC.

SHARE MY STATS
Home > "Share my stats" makes a picture of your season (chests, games, chests a minute, best game,
playtime) and copies it, so you can paste it into Discord with Ctrl+V. A copy is saved in
Pictures\Chest Tracker. It shows only your own numbers and the name you chose.

TASKBAR
Chest Tracker has a button with the chest icon on the taskbar.
- Click it to open the Chest Tracker window.
- Right-click it > Close window to quit Chest Tracker.

THE CHEST TRACKER WINDOW
One window for everything, best between games (the overlay is for during a match):
- Home: your totals, the team's, and this game. Broken chests, console teammates and
  treasures arrive a few seconds after the match: Fortnite locks the replay until then.
- Games: every match with your opened and broken chests (pickaxe or build), the squad's
  total for that game, and treasures.
- Squad: everyone's chests for this game, today and in total, console teammates included.
- Map: routes, chest spots and heatmaps (see MAP WINDOW below).
- Treasures: every treasure hunt prize your squad has found.
- Settings: overlay look (glass or classic, colour themes), locking during matches, the
  mid-match replay check, team code, names.

USING THE OVERLAY
- Drag it with the left mouse button to move it anywhere (outside a match).
- During a match it's LOCKED: clicks go straight through it to the game, so it never gets in
  the way of a fight. To move it mid-match, click Chest Tracker on the taskbar: it unlocks
  until you click back into Fortnite. (Turn this off in settings: "Lock it during matches".)
- Click it to show each player's chests. Click anywhere else to close the list.
- Right-click it for settings:
    Background   how see-through the panel is (0% = just the numbers)
    Size         make the whole overlay bigger or smaller
    Your name, Team code
    Reset this game, Quit
- While Fortnite is in front (lobby or match) the game keeps the mouse, so clicks can't
  reach the overlay. Use the keys instead:
    F9   show/hide everyone's chests
    F11  open settings. This also frees the mouse, so you can drag the overlay.
- The chest count only goes up during a match, never in the lobby.
- When your squad is out (the "#4" placement screen), the overlay says FINAL and shows
  everyone's chests for 30 seconds. Broken chests and console teammates are added a few
  seconds after you leave the match, when Fortnite finishes saving the replay.
- 1080p, 1440p, ultrawide: all fine. It watches the spot where the "+600 XP / CHEST"
  pop-up appears and fine-tunes itself on your first chest. If your first chests don't
  count, press F8 while a pop-up is showing.

HOW IT COUNTS
- While you play: when you open a chest, Fortnite shows "Chest +600 XP". The tracker
  reads that bit of your screen (on your PC only, only while Fortnite is the window
  in front) and counts it right away. Chests opened back to back show one pop-up that
  becomes "+1,200 XP / CHEST X2": the tracker counts the X2 (and X3...) too, and Sprite
  and Rare chest pop-ups separately.
- Miscounting? Set "debug_frames": true in %APPDATA%\ChestTracker\config.json: the
  tracker then saves the pop-up area around each chest pop-up to the debug_frames folder
  there (on your PC only), to see what it read.
- When the match ends: Fortnite finishes saving the match replay. The tracker reads it
  and fixes the game's number: every chest you opened, plus every chest you broke with
  a pickaxe or explosives, or by placing a build on it (a cone over it, a wall through
  it). Those show no pop-up, so only the replay can see them. A build break counts for
  whoever placed the build: each build piece in the replay records its builder.
  The overlay shows "Replay check: 7 opened + 2 broken" and a green tick.
- Each match is only ever counted once, even if you rename or save a replay.
- Match start, squad out and back-to-lobby come from Fortnite's own log file
  (FortniteGame.log), so a new game always starts at 0.
- Teammates on console (no tracker): add their Fortnite names in settings under
  "Console teammates". Your replay holds every teammate's character for the whole match,
  including a flag that's on while they search something, so their chests are counted
  even when they were far away from you. (Ammo boxes look the same as chests in that
  flag, so their count can be a few percent high. Chests they BREAK far from you can't
  be seen.)

TEAM SHARING
- Teammates can be anywhere: different houses, different states. Counts go through
  free public internet relays (public MQTT brokers), usually in under a second.
- Only your chosen name and your chest numbers are shared, plus, during a match, where you
  are on the map for your team's map windows (turn that off with "map_share": false in
  config.json).
- Everything is LOCKED with your team code before it leaves your PC (ChaCha20-Poly1305, with a key
  made from the code): the relays only carry unreadable bytes, and messages without the code are
  thrown away. The code itself is never sent. Anyone who knows your exact code could see your team's
  names and counts, so don't post it publicly, and use a long code ("Make a code").
- People on the same home network also find each other directly (locked the same way).
- Squadmates' counts are the chests they opened. Chests they broke (pickaxe or builds) mostly aren't
  included: your replay only sees the ones broken near you. A squadmate who runs Chest Tracker too
  shares their own full count instead.

It never touches Fortnite's memory or network traffic. It only reads your screen and
files Fortnite itself saves on your PC (replays and its log), so it is as safe as
recording with OBS.

MAP WINDOW
Right-click the overlay > Map window. It's a normal window: put it on a second screen and size
it as you like; it opens in the same place next time.
- This match: your route and a dot for every chest you open, live, read from the minimap in
  the corner of your screen (about 1 m accurate). Teammates who run Chest Tracker too show up
  live. After the match, the replay replaces it with exact routes for the whole squad, chests
  opened (circles), broken (squares), broken with a build (triangles), landing spots and
  eliminations (red X).
- Chest spots: every chest seen opened in your replays is remembered (per season). Live dots
  snap to the nearest known chest, and the ones you looted get a green ring.
- Heatmap: your routes from many games drawn over each other, where you move and spend time,
  land, open chests and get eliminations, over all your replays. Pick yourself, the squad or
  one teammate, and filter by season, period (today, 7 or 30 days), your best or worst games by
  chests, your top games by eliminations, the longest or shortest games, where you landed
  and who you played with.
- Pick any past match and press Play to watch it from above: everyone's path, chests popping
  open and eliminations (a red line from the shooter to where the player went down).
- Plan loot path: tick it and click a landing spot for a route through the chests that are
  most often really there (10-80, slider), round the outside of the island. Drag no-go circles
  it walks round, add numbered checkpoints it visits in order (right-click removes one), and
  pick a Spread: clear the towns, or at most 8, 5 or 3 chests per place so the route spreads
  round the island.
- Mouse wheel zooms, drag to move the map, double-click to follow yourself again.
- The first time, reading all your old replays for the map takes about 10 minutes in the
  background (never while you're in a match). The map picture comes from fortnite-api.com.

TREASURES (Treasure Hunters season)
- When a chest rolls a treasure prize, a gold chest pops out for a moment. The replay records
  who opened it and which prize it was (back bling, loading screen, jam track, gold car decal,
  gold outfit, or Pirate Shop access). After the match the overlay says "TREASURE! You found
  PPPP (jam track)", or "PIRATE SHOP ACCESS!".
- Teammates with the tracker share theirs, so you see "jamie found ..." too.
- Settings > Treasures lists everyone's finds. Your past replays are checked once, in the
  background, after updating.
- Prize names come from a community prize list; weeks 4 and 5 show as "Week 4 Jam Track" etc.
  until real names are added. Sprite gizmos (Relic Finder, Lucky Locator...) drop straight into
  your inventory without a gold chest, so they aren't in this list.
- Like chests, a gold chest only shows up in a replay if it was near the player recording.

LOOK
The numbers use a heavy Fortnite-style font (Impact) with a drop shadow. To use another font,
make a folder called "fonts" next to chest_tracker.py and put one .ttf or .otf file in it.

HOTKEYS
  F6   add a chest by hand (one the tracker missed)
  F7   take one back
  F8   re-learn the pop-up spot (press while a "Chest +600 XP" is showing)
  F9   show/hide everyone's chests
  F11  open settings
  F10  reset "this game" to 0 by hand

KNOWN LIMITS
- Teammates on PC count their own chests with their own tracker. Console teammates are
  counted from your replays (see above).
- Chests broken by a vehicle, or blown up from far away, may not be in the replay.
- A chest you open and then cone counts once, as opened.
- If a Fortnite update changes the replay format, the replay check stops working until
  the reader (reader\ChestReplayReader.exe) is updated. Live counting keeps working.

If something seems off, your settings and a log are in %APPDATA%\ChestTracker
(paste that into the File Explorer address bar). config.json has extra options:
season_start (which replays count toward totals), import_past_replays, internet_sync,
click_through.

Credits: the replay reader is adapted from rossalv/fortnite-chest-counter (MIT) and uses
Shiqan's FortniteReplayDecompressor (MIT). See reader_src\LICENSE-rossalv.txt.

Chest Tracker is a fan-made tool. It isn't made, endorsed or supported by Epic Games. Fortnite is a
trademark of Epic Games, Inc.
