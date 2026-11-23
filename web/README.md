# Squad Chests: the squad's web page

A small web page for the squad, including console friends without Chest Tracker. It shows:

- **The live squad map:** where everyone in the squad is during a match, with their routes, the chests they opened, a
  timeline to scrub back through the match, and full screen with pinch zoom.
- **The squad numbers:** each player's chests this game, today and all time, plus the squad total.

It's a plain static site (`index.html`, `app.js`, `crypto.js`, `map.js`, `style.css`) with no server and no build
step. The page reads the same team relay the trackers use (public MQTT brokers over secure WebSockets), so it works
wherever it's hosted over https (GitHub Pages works: publish this folder).

## Privacy and fair play

- The team code is never sent to the web host. It lives in the part of the link after `#`, which browsers don't send
  to servers.
- Every message on the relay is locked with the team code (`crypto.js`, the same as the tracker's `teamcrypt.py`):
  the page makes the channel name and the key from the code, in the browser, and throws away anything not locked
  with it.
- Anyone who has the link **with** the code can see the squad's numbers and live positions during matches. Only share
  it with the squad.
- Squad only: no other players, no storm, no unopened chest locations. The dots are chests your squad opened.
- Players are Fortnite-style arrows pointing the way they're going; a dashed edge means a console player seen in a
  teammate's replay (about 20 s behind).
- The page only reads. It never sends anything to the relay.

## Updating a hosted copy

Since 1.31 the trackers lock their messages, so a copy of this page from before then shows an empty squad: copy
these files over it.
