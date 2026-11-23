"""Records every match with OBS, keeps the last few, and keeps the whole video of any match with a real find.

How it works:
- When Fortnite's log says a match started, the tracker tells OBS (over OBS's own local control connection,
  obs-websocket, the same one stream decks use) to start recording; when the match ends, to stop.
- After the match, the replay reader says when each treasure prize dropped. For a real prize (anything but a
  sprite gizmo) that match's full video is kept for good, untrimmed, and renamed after the prize, who found it
  and how far into the video it dropped (e.g. "Pirate Shop Access - JayDaAsianDude 2026-11-21 19-29 (at 7m22s).mp4").
- The last KEEP matches are always kept (a find the replay couldn't see is still in one of them); older ones
  the tracker recorded itself are deleted. Files it didn't make are never touched.

OBS does the recording (game capture, microphone, the encoder you picked in OBS). Nothing here reads or
touches the game; the password for OBS's control connection is read from OBS's own settings on this PC and
never leaves it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import struct
import threading
import time
from pathlib import Path

log = logging.getLogger("chest_tracker")

OBS_CONFIG = Path(os.environ.get("APPDATA", ".")) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"
KEEP = 5            # full match videos always kept (the newest ones)
MAX_MATCH_S = 45 * 60  # a recording still running this long after its match started is stopped


# ---------------------------------------------------------------------------------------------------------
# obs-websocket (protocol 5): just enough of a client for start/stop recording, with the standard library
# ---------------------------------------------------------------------------------------------------------
class ObsError(Exception):
    pass


def obs_settings(path: Path = OBS_CONFIG) -> dict | None:
    """OBS's control connection settings on this PC: {"port", "password" (or None)}, or None if it's off."""
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not c.get("server_enabled"):
        return None
    return {"port": int(c.get("server_port") or 4455),
            "password": c.get("server_password") if c.get("auth_required") else None}


def _auth(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient:
    """One short-lived connection to OBS on this PC: connect, send requests, close."""

    def __init__(self, port: int = 4455, password: str | None = None, timeout: float = 5.0):
        self.port, self.password, self.timeout = port, password, timeout
        self.sock: socket.socket | None = None
        self._buf = b""

    # -- websocket framing --------------------------------------------------------------------------------
    def _send(self, obj: dict):
        data = json.dumps(obj).encode()
        head = bytes([0x81])  # final frame, text
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = secrets.token_bytes(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ObsError("OBS closed the connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _recv(self) -> dict:
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            op = b0 & 0x0F
            if op == 0x8:
                raise ObsError("OBS closed the connection")
            if op == 0x9:  # ping
                continue
            if op in (0x1, 0x0):
                return json.loads(data.decode("utf-8"))

    # -- protocol ----------------------------------------------------------------------------------------------
    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           f"Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError("OBS refused the connection")
            self._buf += chunk
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ObsError("OBS refused the connection")
        hello = self._recv()
        if hello.get("op") != 0:
            raise ObsError("unexpected answer from OBS")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = (hello.get("d") or {}).get("authentication")
        if auth:
            if not self.password:
                raise ObsError("OBS wants a password and none is set in OBS's WebSocket settings")
            ident["authentication"] = _auth(self.password, auth["salt"], auth["challenge"])
        self._send({"op": 1, "d": ident})
        while True:
            m = self._recv()
            if m.get("op") == 2:
                return self
            if m.get("op") not in (5,):
                raise ObsError("OBS didn't accept the connection (wrong password?)")

    def request(self, kind: str, data: dict | None = None) -> dict:
        rid = secrets.token_hex(6)
        self._send({"op": 6, "d": {"requestType": kind, "requestId": rid, **({"requestData": data} if data else {})}})
        while True:
            m = self._recv()
            d = m.get("d") or {}
            if m.get("op") == 7 and d.get("requestId") == rid:
                st = d.get("requestStatus") or {}
                if not st.get("result"):
                    raise ObsError(f"{kind}: {st.get('comment') or st.get('code')}")
                return d.get("responseData") or {}

    def close(self):
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass
        self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()


def obs_client() -> ObsClient:
    s = obs_settings()
    if s is None:
        raise ObsError("OBS's WebSocket server is off (OBS: Tools > WebSocket Server Settings)")
    return ObsClient(s["port"], s["password"])


def _move(src: Path, dst: Path) -> Path:
    """Renames src to dst (adding " (2)", " (3)"... if that name is taken). Returns the new path."""
    n, out = 2, dst
    while out.exists():
        out = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
        n += 1
    os.replace(src, out)
    return out


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "", text).strip()[:60] or "Treasure"


# ---------------------------------------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------------------------------------
class MatchRecorder:
    """Starts/stops OBS recording with the match, remembers each recording, keeps the newest KEEP, and keeps
    (and renames) every match video with a real find. All OBS calls run on a worker thread (never on the window
    or the log thread)."""

    def __init__(self, app, index: Path, client_factory=obs_client, keep: int = KEEP):
        self.app = app
        self.index_path = index
        self.client_factory, self.keep = client_factory, keep
        self.lock = threading.Lock()
        try:
            d = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        self.recordings: list[dict] = list(d.get("recordings") or [])
        self.status = "off"
        self._jobs: list = []
        self._wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True, name="match-recorder").start()

    def enabled(self) -> bool:
        return bool(self.app.cfg.get("record_matches"))

    # -- events (from the tracker) ----------------------------------------------------------------------------
    def match_started(self):
        if self.enabled():
            self._queue(self._start)

    def match_over(self):
        if self.enabled() or self._active():
            self._queue(self._stop)

    def replay_read(self, replay_name: str, started: float, treasures_found: list, describe):
        """A replay was read: a real prize in it (anyone in the squad) keeps that match's whole video."""
        real = []
        for t in treasures_found or []:
            if not isinstance(t, dict) or t.get("won") is False:
                continue
            d = describe(t.get("prize"))
            if d.get("kind") != "sprite gizmo":
                real.append((float(t.get("t") or 0), d["name"], str(t.get("name") or "")))
        if real and started:
            self._queue(lambda: self._keep_find(replay_name, float(started), real))

    def shared_find(self, when: float, prize: str, who: str, describe):
        """A teammate's own tracker shared a find. Their replay saw it even when mine didn't (they were too far
        away: Nov 22, 2026, Falcon's loading screen video was deleted), so it keeps that match's video too."""
        d = describe(prize)
        if d.get("kind") == "sprite gizmo" or not when:
            return
        self._queue(lambda: self._keep_at(float(when), d["name"], str(who or "")))

    def test_clip(self, done=None):
        """Settings' "Test recording": records 10 s and saves it as "Chest Tracker test recording.mp4"."""
        def job():
            try:
                with self.client_factory() as c:
                    was = c.request("GetRecordStatus").get("outputActive")
                    if not was:
                        c.request("StartRecord")
                        for _ in range(20):
                            if c.request("GetRecordStatus").get("outputActive"):
                                break
                            time.sleep(0.25)
                        else:
                            raise ObsError("OBS didn't start recording (close OBS's Settings window and try again)")
                time.sleep(10)
                with self.client_factory() as c:
                    path = c.request("StopRecord").get("outputPath") if not was else None
                if not path:
                    msg = "OBS was already recording: stop it and try again"
                else:
                    src = Path(path)
                    old = src.with_name("Chest Tracker test recording.mp4")
                    try:
                        old.unlink()  # (the last test)
                    except OSError:
                        pass
                    try:
                        src = _move(src, old)
                    except OSError:
                        pass
                    msg = f"Test recording saved: {src}"
            except Exception as e:
                msg = f"OBS test failed: {e}"
            log.info("recording test: %s", msg)
            if done:
                done(msg)
        self._queue(job)

    # -- worker ------------------------------------------------------------------------------------------------
    def _queue(self, job):
        with self.lock:
            self._jobs.append(job)
        self._wake.set()

    def _worker(self):
        while True:
            self._wake.wait(30)
            self._wake.clear()
            while True:
                with self.lock:
                    job = self._jobs.pop(0) if self._jobs else None
                if job is None:
                    break
                try:
                    job()
                except Exception as e:
                    log.warning("match recording: %s", e)
                    self.status = f"OBS: {e}"
            self._stop_if_too_long()

    def _active(self) -> dict | None:
        with self.lock:
            return next((r for r in reversed(self.recordings) if r.get("recording")), None)

    def _start(self):
        if self._active():
            self._stop()  # (the last match's never got its "over": requeued straight from the bus)
        with self.client_factory() as c:
            st = c.request("GetRecordStatus")
            if st.get("outputActive"):
                self.status = "OBS was already recording (you started it): not touching it"
                log.info("match recording: OBS is already recording, so this match isn't recorded separately")
                return
            c.request("StartRecord")
            began = None
            for _ in range(20):
                st = c.request("GetRecordStatus")
                if st.get("outputActive"):
                    began = time.time() - float(st.get("outputDuration") or 0) / 1000
                    break
                time.sleep(0.25)
        if began is None:
            # (Nov 21, 2026: OBS accepts the request but doesn't record while its Settings window is open)
            self.status = "OBS didn't start recording (is its Settings window open?)"
            log.warning("match recording: OBS didn't start recording (Settings window open, or no disk space?)")
            if hasattr(self.app, "toast"):
                self.app.toast("OBS didn't start recording this match (close OBS's Settings window)", 10)
            return
        with self.lock:
            self.recordings.append({"start": round(began, 2), "recording": True})
        self.status = "recording this match"
        log.info("match recording started")
        self._save()

    def _stop(self):
        r = self._active()
        if r is None:
            return
        with self.client_factory() as c:
            st = c.request("GetRecordStatus")
            path = c.request("StopRecord").get("outputPath") if st.get("outputActive") else None
        with self.lock:
            r.pop("recording", None)
            r["end"] = round(time.time(), 2)
            if path:
                r["path"] = path
            else:
                self.recordings.remove(r)  # (stopped in OBS by hand: nothing of ours to keep track of)
        self.status = "last match recorded" if path else "OBS recording was stopped by hand"
        log.info("match recording saved: %s", path)
        if path and r.get("finds"):  # (a teammate's find came in while it was still recording)
            self._name_kept(r, [(f["at"], f["name"], f["who"]) for f in r["finds"]])
        self._save()
        self._prune()

    def _stop_if_too_long(self):
        r = self._active()
        if r is not None and time.time() - float(r.get("start") or 0) > MAX_MATCH_S:
            try:
                self._stop()
            except Exception as e:
                log.warning("match recording: %s", e)

    def _recording_for(self, started: float, length: float = 0.0) -> dict | None:
        """The recording that was running while a replay's match was being played."""
        with self.lock:
            best = None
            for r in self.recordings:
                if not r.get("path"):
                    continue
                s, e = float(r["start"]), float(r.get("end") or r["start"])
                if s - 180 <= started <= e:
                    best = r
            return best

    def _keep_find(self, replay_name: str, started: float, finds: list):
        r = self._recording_for(started)
        if r is None:
            log.info("treasure in %s, but no recording of that match", replay_name)
            return
        self._add_finds(r, [(started + t - float(r["start"]), name, who) for t, name, who in finds])

    def _keep_at(self, when: float, name: str, who: str):
        """A find at a known clock time (shared by a teammate): the recording running then, finished or not."""
        with self.lock:
            r = next((x for x in reversed(self.recordings)
                      if float(x["start"]) - 5 <= when <= float(x.get("end") or time.time()) + 5
                      and (x.get("path") or x.get("recording"))), None)
        if r is None:
            log.info("%s found %s, but no recording of that match", who or "a teammate", name)
            return
        self._add_finds(r, [(when - float(r["start"]), name, who)])

    def _add_finds(self, r: dict, finds: list):
        """Marks the match's video kept (for good, untrimmed) and names it after its first find."""
        with self.lock:
            r["keep"] = True
            r.setdefault("finds", [])
            new = []
            for at, name, who in finds:  # (at: seconds into the video)
                at = max(0.0, at)
                # (the same find seen in my replay and shared by its finder: a second or two apart)
                if not any(abs(f.get("at", -1e9) - at) < 1 or (f.get("name") == name and abs(f["at"] - at) < 10)
                           for f in r["finds"]):
                    r["finds"].append({"at": round(at, 1), "name": name, "who": who})
                    new.append((at, name, who))
        if not new:
            return
        if r.get("recording"):  # (still recording: it's named when the match ends)
            for at, name, who in new:
                log.info("treasure: %s%s at %d:%02d into this match's video (whole match kept)", name,
                         f" ({who})" if who else "", int(at // 60), int(at % 60))
            if hasattr(self.app, "toast"):
                self.app.toast(f"This match's video will be kept: {new[0][1]}", 10)
            self._save()
            return
        self._name_kept(r, new)

    def _name_kept(self, r: dict, new: list):
        with self.lock:
            first = min(r["finds"], key=lambda f: f["at"]) if r.get("finds") else None
        src = Path(r["path"])
        if not r.get("renamed") and first and src.exists():
            at = max(0, int(first["at"]))
            stamp = time.strftime("%Y-%m-%d %H-%M", time.localtime(float(r["start"])))
            who = f" - {safe_name(first['who'])}" if first["who"] else ""
            dst = src.with_name(f"{safe_name(first['name'])}{who} {stamp} (at {at // 60}m{at % 60:02d}s){src.suffix}")
            try:
                src = _move(src, dst)
                with self.lock:
                    r["path"], r["renamed"] = str(src), True
            except OSError as e:
                log.info("kept %s but couldn't rename it: %s", src.name, e)
        for at, name, who in new:
            log.info("treasure: %s%s at %d:%02d into %s (whole match kept)", name, f" ({who})" if who else "",
                     int(at // 60), int(at % 60), src.name)
        if hasattr(self.app, "toast"):
            at, name, _ = new[0]
            self.app.toast(f"Match video kept: {name} at {int(at // 60)}:{int(at % 60):02d} into {src.name}", 12)
        self._save()

    def _prune(self):
        """Keeps the newest `keep` match videos and every one with a real find; deletes the rest (only files
        this tracker recorded)."""
        with self.lock:
            done = [r for r in self.recordings if r.get("path") and not r.get("recording")]
            newest = set(id(r) for r in done[-self.keep:])
            drop = [r for r in done if id(r) not in newest and not r.get("keep")]
            for r in drop:
                try:
                    Path(r["path"]).unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    log.info("couldn't delete old match video %s: %s", r["path"], e)
                    continue
                self.recordings.remove(r)
        if drop:
            log.info("deleted %d old match videos (keeping the last %d)", len(drop), self.keep)
            self._save()

    def _save(self):
        with self.lock:
            text = json.dumps({"recordings": self.recordings}, indent=1)
        try:
            tmp = self.index_path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.index_path)
        except OSError as e:
            log.info("couldn't save the recordings list: %s", e)
