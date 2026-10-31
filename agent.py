# OxLog agent mode — watches the plugin folder, records sub-versions, tracks compile results
import os, re, json, time, hashlib, hmac, shutil, secrets, threading
from datetime import datetime
from flask import request, session, jsonify

AGENT_KEY_FILE = ".agent_key"
STATE_FILE = "subversions.json"
MAX_EVENTS = 500
COMPILE_TIMEOUT = 90  # seconds before a pending compile is marked unknown

RE_COMPILED = re.compile(r"\] (.+?) was compiled successfully in (\d+)ms")
RE_COMPILE_ERR = re.compile(r"\] Error while compiling (.+?): (.*)$")
RE_LOADED = re.compile(r"\] Loaded plugin (.+?) v([\d.]+) by")
RE_RUNTIME_ERR = re.compile(r"\[Error\] .*?plugin '(.+?) v[\d.]+'")


def get_agent_key():
    if os.path.exists(AGENT_KEY_FILE):
        with open(AGENT_KEY_FILE, "r") as f:
            return f.read().strip()
    key = secrets.token_urlsafe(32)
    with open(AGENT_KEY_FILE, "w") as f:
        f.write(key)
    return key


def file_sha(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Agent:
    def __init__(self, load_config, get_versions_dir, parse_plugin_info):
        self.load_config = load_config
        self.get_versions_dir = get_versions_dir
        self.parse_plugin_info = parse_plugin_info
        self.key = get_agent_key()
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.entries = []
        self.known = {}          # file -> sha of last recorded state
        self.events = []
        self.event_id = 0
        self.pending_source = {} # file -> expiry; set by prewrite so the next change is tagged Claude
        self.suppress = {}       # file -> expiry; OxLog's own writes (log update, revert)
        self._config = None
        self._config_at = 0
        self.load_state()

    # ---------- state ----------

    def load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.entries = state.get("entries", [])
            self.known = state.get("known", {})
        except Exception:
            pass
        # Anything still pending from a previous run never got a result
        for e in self.entries:
            if e.get("compile", {}).get("status") == "pending":
                e["compile"] = {"status": "unknown"}

    def save_state(self):
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"known": self.known, "entries": self.entries}, f, indent=1)
        os.replace(tmp, STATE_FILE)

    def config(self):
        # The watcher polls constantly; don't re-read config.json every second
        if self._config is None or time.time() - self._config_at > 5:
            try:
                self._config = self.load_config()
                self._config_at = time.time()
            except Exception:
                pass
        return self._config or {}

    def plugin_dir(self):
        return self.config().get("plugin_dir", "")

    def plugin_name_for(self, fname):
        for p in self.config().get("plugins", []):
            if p.get("file") == fname:
                return p["name"]
        return os.path.splitext(fname)[0]

    def push_event(self, kind, fname, text, entry_id=None):
        with self.cond:
            self.event_id += 1
            self.events.append({
                "id": self.event_id, "ts": now_ts(), "kind": kind,
                "file": fname, "plugin": self.plugin_name_for(fname) if fname else "",
                "text": text, "entry": entry_id
            })
            del self.events[:-MAX_EVENTS]
            self.cond.notify_all()

    # ---------- sub-versions ----------

    def record_entry(self, fname, kind, source):
        """Snapshot the current file as a sub-version. kind: Claude, Manual or Baseline."""
        with self.cond:
            path = os.path.join(self.plugin_dir(), fname)
            sha = file_sha(path)
            info = self.parse_plugin_info(path)
            base = ".".join(str(x) for x in info["version"]) if info else "0.0.0"
            pname = self.plugin_name_for(fname)
            if kind == "Baseline":
                version = f"v{base}"
            else:
                n = 1 + sum(1 for e in self.entries
                            if e["file"] == fname and not e.get("released")
                            and e["base"] == base and e["kind"] != "Baseline")
                version = f"v{base}.{n}"
            ts = now_ts()
            safe_ts = ts.replace(":", "-").replace(" ", "_")
            folder = os.path.join(self.get_versions_dir(), pname, f"{version} - {kind} - {safe_ts}")
            os.makedirs(folder, exist_ok=True)
            shutil.copy2(path, os.path.join(folder, fname))
            with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as f:
                f.write(f"{pname} {version} — {kind}\n{ts}\n\n")
            entry = {
                "id": f"{int(time.time() * 1000)}-{len(self.entries)}",
                "plugin": pname, "title": info["name"] if info else pname,
                "file": fname, "base": base, "version": version, "kind": kind,
                "source": source, "ts": ts, "epoch": time.time(), "folder": folder,
                "sha": sha, "notes": "", "released": False,
                "compile": {"status": "n/a" if kind == "Baseline" else "pending"}
            }
            self.entries.append(entry)
            self.known[fname] = sha
            self.save_state()
            if kind != "Baseline":
                self.push_event("version", fname, f"{pname} {version} saved ({kind})", entry["id"])
            return entry

    def latest_entry(self, fname):
        for e in reversed(self.entries):
            if e["file"] == fname:
                return e
        return None

    def mark_known(self, fname):
        """Called after OxLog itself writes a plugin file so the watcher doesn't record it."""
        path = os.path.join(self.plugin_dir(), fname)
        with self.cond:
            try:
                self.known[fname] = file_sha(path)
                self.save_state()
            except OSError:
                pass
            self.suppress.pop(fname, None)

    def suppress_file(self, fname, seconds=30):
        with self.cond:
            self.suppress[fname] = time.time() + seconds

    def release(self, fname, version_str):
        """A Log Update folds the plugin's sub-versions into a real release."""
        with self.cond:
            for e in self.entries:
                if e["file"] == fname and not e.get("released"):
                    e["released"] = True
                    e["released_in"] = version_str
            self.save_state()
        self.mark_known(fname)
        self.push_event("release", fname, f"{self.plugin_name_for(fname)} released as {version_str}")

    def note_revert(self, fname, version_str):
        self.mark_known(fname)
        self.push_event("revert", fname, f"{self.plugin_name_for(fname)} reverted to {version_str} by user")

    # ---------- watcher ----------

    def baseline(self):
        pdir = self.plugin_dir()
        if not os.path.isdir(pdir):
            return
        changed = 0
        for fname in sorted(os.listdir(pdir)):
            if not fname.endswith(".cs"):
                continue
            path = os.path.join(pdir, fname)
            try:
                sha = file_sha(path)
                if fname not in self.known:
                    self.record_entry(fname, "Baseline", "startup")
                elif self.known[fname] != sha:
                    self.record_entry(fname, "Manual", "changed while OxLog was off")
                    changed += 1
            except Exception as ex:
                print(f"  [agent] baseline failed for {fname}: {ex}")
        if changed:
            print(f"  [agent] {changed} plugin(s) changed while OxLog was off")

    def watch_loop(self):
        try:
            self.baseline()
        except Exception as ex:
            print(f"  [agent] baseline failed: {ex}")
        stats, dirty = {}, {}
        while True:
            time.sleep(1)
            try:
                if not self.config().get("agent_watcher", True):
                    continue
                pdir = self.plugin_dir()
                if not os.path.isdir(pdir):
                    continue
                now = time.time()
                for fname in os.listdir(pdir):
                    if not fname.endswith(".cs"):
                        continue
                    try:
                        st = os.stat(os.path.join(pdir, fname))
                    except OSError:
                        continue
                    key = (st.st_mtime, st.st_size)
                    prev = stats.get(fname)
                    stats[fname] = key
                    if prev is not None and prev != key:
                        dirty[fname] = now
                    elif prev is None and fname not in self.known:
                        dirty[fname] = now  # new plugin file
                # Process files that have been stable for a second
                for fname, t in list(dirty.items()):
                    if now - t < 1.0:
                        continue
                    del dirty[fname]
                    self.process_change(fname)
                self.expire_pending()
            except Exception as ex:
                print(f"  [agent] watcher error: {ex}")

    def process_change(self, fname):
        path = os.path.join(self.plugin_dir(), fname)
        if not os.path.exists(path):
            return
        sha = file_sha(path)
        with self.cond:
            if self.known.get(fname) == sha:
                return
            if self.suppress.get(fname, 0) > time.time():
                return  # OxLog's own write; mark_known will follow
            claude = self.pending_source.pop(fname, 0) > time.time()
        self.record_entry(fname, "Claude" if claude else "Manual", "claude" if claude else "file change")

    def expire_pending(self):
        with self.cond:
            changed = False
            for e in self.entries[-50:]:
                c = e.get("compile", {})
                if c.get("status") == "pending" and time.time() - e.get("epoch", 0) > COMPILE_TIMEOUT:
                    e["compile"] = {"status": "unknown", "msg": "No compile result seen in the Oxide log"}
                    changed = True
                    self.cond.notify_all()
            if changed:
                self.save_state()

    # ---------- Oxide log tail ----------

    def tail_loop(self):
        cur, pos, buf = None, 0, b""
        while True:
            time.sleep(0.5)
            try:
                pdir = self.plugin_dir()
                if not pdir:
                    continue
                logs_dir = os.path.join(os.path.dirname(pdir), "logs")
                path = os.path.join(logs_dir, f"oxide_{datetime.now():%Y-%m-%d}.txt")
                if not os.path.exists(path):
                    continue
                size = os.path.getsize(path)
                if path != cur:
                    # Start at the end on first run; read a new day's file from the top
                    pos = size if cur is None else 0
                    cur, buf = path, b""
                if size < pos:
                    pos, buf = 0, b""
                if size == pos:
                    continue
                with open(path, "rb") as f:
                    f.seek(pos)
                    data = f.read()
                pos += len(data)
                lines = (buf + data).split(b"\n")
                buf = lines.pop()
                for raw in lines:
                    self.handle_log_line(raw.decode("utf-8", errors="replace").rstrip("\r"))
            except Exception as ex:
                print(f"  [agent] log tail error: {ex}")

    def pending_for(self, fname=None, title=None, statuses=("pending",)):
        for e in reversed(self.entries):
            if (fname and e["file"] == fname) or (title and e.get("title") == title):
                if e.get("compile", {}).get("status") in statuses:
                    return e
                return None
        return None

    def handle_log_line(self, line):
        m = RE_COMPILED.search(line)
        if m:
            fname = m.group(1) + ".cs"
            with self.cond:
                e = self.pending_for(fname=fname)
                if e:
                    e["compile"] = {"status": "ok", "ms": int(m.group(2))}
                    self.save_state()
            self.push_event("compiled", fname, f"{m.group(1)} compiled in {m.group(2)}ms", e["id"] if e else None)
            return
        m = RE_COMPILE_ERR.search(line)
        if m:
            fname = m.group(1) + ".cs"
            with self.cond:
                e = self.pending_for(fname=fname)
                if e:
                    e["compile"] = {"status": "error", "msg": m.group(2)}
                    self.save_state()
            self.push_event("compile_error", fname, f"{m.group(1)}: {m.group(2)}", e["id"] if e else None)
            return
        m = RE_LOADED.search(line)
        if m:
            with self.cond:
                e = self.pending_for(title=m.group(1), statuses=("ok", "pending"))
                if e:
                    e["compile"]["status"] = "ok"
                    e["compile"]["loaded"] = m.group(2)
                    self.save_state()
                fname = e["file"] if e else ""
            self.push_event("loaded", fname, f"Loaded {m.group(1)} v{m.group(2)}", e["id"] if e else None)
            return
        m = RE_RUNTIME_ERR.search(line)
        if m:
            with self.cond:
                e = self.pending_for(title=m.group(1), statuses=("ok",))
                if e:
                    e.setdefault("runtime_errors", [])
                    if len(e["runtime_errors"]) < 20:
                        e["runtime_errors"].append(line[:500])
                    self.save_state()
                fname = e["file"] if e else ""
            self.push_event("runtime_error", fname, line[:300], e["id"] if e else None)

    def start(self):
        threading.Thread(target=self.watch_loop, daemon=True, name="agent-watch").start()
        threading.Thread(target=self.tail_loop, daemon=True, name="agent-tail").start()

    # ---------- API ----------

    def authed(self):
        if session.get("authed"):
            return True
        if not self.config().get("agent_api", True):
            return False
        supplied = request.headers.get("X-OxLog-Key", "")
        return bool(supplied) and hmac.compare_digest(supplied, self.key)

    def resolve_file(self, raw):
        fname = os.path.basename((raw or "").replace("\\", "/"))
        if not fname.endswith(".cs"):
            return None
        return fname

    def entry_view(self, e):
        if not e:
            return None
        return {k: e.get(k) for k in ("id", "plugin", "file", "version", "kind", "source", "ts",
                                      "compile", "runtime_errors", "released", "notes", "folder")}

    def history_entries(self, plugin=""):
        """Unreleased sub-versions, shaped like /api/history entries."""
        with self.lock:
            live = [e for e in self.entries if not e.get("released") and (not plugin or e["plugin"] == plugin)]
            # Only show a plugin's baseline once it has sub-versions on top of it
            active = {e["file"] for e in live if e["kind"] != "Baseline"}
            out = []
            for e in live:
                if e["kind"] == "Baseline" and e["file"] not in active:
                    continue
                out.append({
                    "plugin": e["plugin"], "version": e["version"], "type": e["kind"],
                    "ts": e["ts"], "folder": e["folder"], "notes": e.get("notes", ""),
                    "sub": True, "source": e["source"], "compile": e.get("compile", {}),
                    "runtime_errors": len(e.get("runtime_errors", []))
                })
            return out

    def register_routes(self, app):
        agent = self

        @app.route("/api/agent/prewrite", methods=["POST"])
        def agent_prewrite():
            if not agent.authed():
                return jsonify({"ok": False, "msg": "Unauthorized"}), 401
            fname = agent.resolve_file((request.json or {}).get("file"))
            if not fname:
                return jsonify({"ok": False, "msg": "Only .cs plugin files are tracked"}), 400
            path = os.path.join(agent.plugin_dir(), fname)
            snapshot = None
            try:
                # Back up the current state first if it was never recorded
                if os.path.exists(path) and agent.known.get(fname) != file_sha(path):
                    kind = "Manual" if fname in agent.known else "Baseline"
                    snapshot = agent.record_entry(fname, kind, "pre-write")["folder"]
            except Exception as ex:
                return jsonify({"ok": False, "msg": f"Backup failed: {ex}"}), 500
            with agent.cond:
                agent.pending_source[fname] = time.time() + 120
            last = agent.latest_entry(fname)
            reverted = [ev for ev in agent.events if ev["kind"] == "revert" and ev["file"] == fname]
            return jsonify({"ok": True, "snapshot": snapshot, "latest": agent.entry_view(last),
                            "last_revert": reverted[-1] if reverted else None})

        @app.route("/api/agent/status", methods=["GET"])
        def agent_status():
            if not agent.authed():
                return jsonify({"ok": False, "msg": "Unauthorized"}), 401
            fname = agent.resolve_file(request.args.get("file"))
            if not fname:
                return jsonify({"ok": False, "msg": "Only .cs plugin files are tracked"}), 400
            wait = min(float(request.args.get("wait", 0) or 0), 45)
            path = os.path.join(agent.plugin_dir(), fname)
            deadline = time.time() + wait
            with agent.cond:
                while True:
                    e = agent.latest_entry(fname)
                    try:
                        current = file_sha(path) if os.path.exists(path) else None
                    except OSError:
                        current = None
                    settled = e is not None and e["sha"] == current and e["compile"].get("status") != "pending"
                    # After a compile, give Oxide a moment to log the load or a runtime error
                    if settled and e["compile"].get("status") == "ok" and "loaded" not in e["compile"] \
                            and time.time() - e.get("epoch", 0) < 20:
                        settled = False
                    remaining = deadline - time.time()
                    if settled or remaining <= 0:
                        break
                    agent.cond.wait(min(remaining, 1.0))
                events = [ev for ev in agent.events if ev["file"] == fname][-10:]
            return jsonify({"ok": True, "file": fname, "settled": settled, "latest": agent.entry_view(e),
                            "unrecorded_changes": e is None or e["sha"] != current, "events": events})

        @app.route("/api/agent/note", methods=["POST"])
        def agent_note():
            if not agent.authed():
                return jsonify({"ok": False, "msg": "Unauthorized"}), 401
            data = request.json or {}
            fname = agent.resolve_file(data.get("file"))
            notes = (data.get("notes") or "").strip()
            with agent.cond:
                e = agent.latest_entry(fname) if fname else None
                if not e or e["kind"] == "Baseline":
                    return jsonify({"ok": False, "msg": "No sub-version to annotate"})
                e["notes"] = notes
                agent.save_state()
                try:
                    with open(os.path.join(e["folder"], "notes.txt"), "w", encoding="utf-8") as f:
                        f.write(f"{e['plugin']} {e['version']} — {e['kind']}\n{e['ts']}\n\n{notes}")
                except OSError:
                    pass
            agent.push_event("note", fname, f"{e['plugin']} {e['version']}: {notes[:120]}", e["id"])
            return jsonify({"ok": True, "version": e["version"]})

        @app.route("/api/agent/events", methods=["GET"])
        def agent_events():
            if not agent.authed():
                return jsonify({"ok": False}), 401
            since = int(request.args.get("since", 0) or 0)
            with agent.lock:
                evs = [ev for ev in agent.events if ev["id"] > since]
                return jsonify({"ok": True, "events": evs[-100:], "last_id": agent.event_id})

        @app.route("/api/agent/restart", methods=["POST"])
        def agent_restart():
            if not agent.authed():
                return jsonify({"ok": False}), 401
            def do_exit():
                time.sleep(1)
                os._exit(0)  # start.bat brings OxLog back up
            threading.Thread(target=do_exit, daemon=True).start()
            return jsonify({"ok": True})
