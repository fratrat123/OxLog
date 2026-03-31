# OxLog — Oxide Plugin Changelog
import os, re, subprocess, shutil
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, redirect, session, jsonify
import json, requests, difflib
from datetime import datetime

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

SECRET_KEY_FILE = ".secret_key"
def get_secret_key():
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, "rb") as f:
            return f.read()
    key = os.urandom(24)
    with open(SECRET_KEY_FILE, "wb") as f:
        f.write(key)
    return key

app.secret_key = get_secret_key()

CONFIG_FILE = "config.json"
LOG_FILE = "plugin_changelog.txt"
VERSIONS_DIR = "versions"
OXLOG_VERSION = "1.0.6"
UPDATE_URL = "https://raw.githubusercontent.com/fratrat123/OxLog/main/version.json"

DEFAULT_CONFIG = {
    "pin": "",
    "app_name": "OxLog",
    "plugin_dir": "",
    "archive_dir": "",
    "groups": [],
    "plugins": [],
    "rcon_host": "",
    "rcon_port": 28016,
    "rcon_password": "",
    "setup_complete": False,
    "tutorial_complete": False,
    "update_url": ""
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        if k not in config:
            config[k] = v
    return config

def is_setup_done():
    config = load_config()
    return config.get("setup_complete", False)

def save_config(config):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    if os.path.exists(CONFIG_FILE):
        os.replace(CONFIG_FILE, CONFIG_FILE + ".bak")
    os.replace(tmp, CONFIG_FILE)

def parse_plugin_info(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(2000)
        match = re.search(r'\[Info\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"', content)
        if match:
            return {
                "name": match.group(1),
                "author": match.group(2),
                "version": [int(x) for x in match.group(3).split(".")],
                "file": os.path.basename(filepath)
            }
    except Exception:
        pass
    return None

def scan_plugins(plugin_dir):
    results = []
    if not os.path.isdir(plugin_dir):
        return results
    for fname in sorted(os.listdir(plugin_dir)):
        if fname.endswith(".cs"):
            info = parse_plugin_info(os.path.join(plugin_dir, fname))
            if info:
                results.append(info)
    return results

def update_version_in_file(filepath, new_version):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        version_str = ".".join(str(x) for x in new_version)
        updated = re.sub(
            r'(\[Info\s*\(\s*"[^"]+"\s*,\s*"[^"]+"\s*,\s*)"[^"]+"',
            rf'\g<1>"{version_str}"',
            content
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
        return True
    except Exception:
        return False

def get_versions_dir():
    """Return the versions directory — uses backup dir if set, otherwise local"""
    config = load_config()
    archive = config.get("archive_dir", "")
    if archive:
        return os.path.join(archive, "versions")
    return VERSIONS_DIR

def discover_plugin_files(plugin_name, plugin_dir):
    """Find config and data files associated with a plugin"""
    oxide_root = os.path.dirname(plugin_dir)
    config_dir = os.path.join(oxide_root, "config")
    data_dir = os.path.join(oxide_root, "data")
    files = []  # list of (src_path, relative_snapshot_path)

    # Config file
    config_file = plugin_name + ".json"
    config_path = os.path.join(config_dir, config_file)
    if os.path.exists(config_path):
        files.append((config_path, os.path.join("config", config_file)))

    # Data files
    if os.path.isdir(data_dir):
        data_json = plugin_name + ".json"
        data_path = os.path.join(data_dir, data_json)
        if os.path.exists(data_path):
            files.append((data_path, os.path.join("data", data_json)))
        data_subfolder = os.path.join(data_dir, plugin_name)
        if os.path.isdir(data_subfolder):
            try:
                for fname in sorted(os.listdir(data_subfolder)):
                    fpath = os.path.join(data_subfolder, fname)
                    if os.path.isfile(fpath):
                        files.append((fpath, os.path.join("data", plugin_name, fname)))
            except PermissionError:
                pass
        # Parse .cs for data file references
        cs_path = os.path.join(plugin_dir, plugin_name + ".cs")
        if os.path.exists(cs_path):
            try:
                with open(cs_path, "r", encoding="utf-8", errors="ignore") as f:
                    source = f.read()
                refs = set(re.findall(r'(?:GetFile|GetDatafile|ReadObject|WriteObject)\s*(?:<[^>]*>)?\s*\(\s*"([^"]+)"', source))
                existing = {f[0] for f in files}
                for ref in sorted(refs):
                    ref_path = os.path.join(data_dir, ref + ".json") if not ref.endswith(".json") else os.path.join(data_dir, ref)
                    ref_name = ref + ".json" if not ref.endswith(".json") else ref
                    if os.path.exists(ref_path) and ref_path not in existing:
                        files.append((ref_path, os.path.join("data", ref_name)))
            except Exception:
                pass
    return files

def snapshot_plugin(plugin_dir, plugin_file, plugin_name, version, update_type, notes, ts):
    try:
        version_str = "v" + ".".join(str(x) for x in version)
        safe_ts = ts.replace(":", "-").replace(" ", "_")
        folder_name = f"{version_str} - {update_type} - {safe_ts}"
        snapshot_dir = os.path.join(get_versions_dir(), plugin_name, folder_name)
        os.makedirs(snapshot_dir, exist_ok=True)
        # Copy plugin .cs
        src = os.path.join(plugin_dir, plugin_file)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(snapshot_dir, plugin_file))
        # Copy config and data files
        for src_path, rel_path in discover_plugin_files(plugin_name, plugin_dir):
            dest = os.path.join(snapshot_dir, rel_path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src_path, dest)
        # Write notes
        with open(os.path.join(snapshot_dir, "notes.txt"), "w", encoding="utf-8") as f:
            f.write(f"{plugin_name} {version_str} — {update_type}\n{ts}\n\n{notes}")
        return snapshot_dir
    except Exception:
        return None

@app.route("/", methods=["GET"])
def index():
    if not is_setup_done():
        return redirect("/setup")
    if not session.get("authed"):
        return redirect("/login")
    config = load_config()
    just_updated = False
    flag = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".updated")
    if os.path.exists(flag):
        just_updated = True
        os.remove(flag)
    return render_template("index.html", plugins=config["plugins"], show_tutorial=not config.get("tutorial_complete", False), version=OXLOG_VERSION, just_updated=just_updated)

@app.route("/setup", methods=["GET"])
def setup():
    if is_setup_done():
        return redirect("/login")
    return render_template("setup.html")

@app.route("/api/browse", methods=["POST"])
def browse_dirs():
    # Allow during setup (no auth) or when authed
    if is_setup_done() and not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    path = data.get("path", "")
    try:
        if not path:
            # Return drive letters on Windows
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.isdir(drive):
                    drives.append(drive)
            return jsonify({"ok": True, "path": "", "dirs": drives, "parent": ""})
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return jsonify({"ok": False, "msg": "Not a valid directory"})
        dirs = []
        try:
            for item in sorted(os.listdir(path)):
                full = os.path.join(path, item)
                if os.path.isdir(full) and not item.startswith('.'):
                    dirs.append(item)
        except PermissionError:
            pass
        parent = os.path.dirname(path)
        if parent == path:
            parent = ""
        return jsonify({"ok": True, "path": path, "dirs": dirs, "parent": parent})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/detect/oxide", methods=["GET"])
def detect_oxide():
    """Scan all drives for oxide/plugins folders"""
    import string
    found = []
    # Common Rust server paths to check first
    quick_checks = [
        r"C:\rustserver\oxide\plugins",
        r"C:\rustserver\server\oxide\plugins",
        r"D:\rustserver\oxide\plugins",
        r"D:\rustserver\server\oxide\plugins",
        r"C:\Server\oxide\plugins",
        r"D:\Server\oxide\plugins",
    ]
    for path in quick_checks:
        if os.path.isdir(path):
            found.append(path)
    if found:
        return jsonify({"ok": True, "paths": found})

    # Deeper scan — walk top 3 levels of each drive
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if not os.path.isdir(drive):
            continue
        try:
            for d1 in os.listdir(drive):
                p1 = os.path.join(drive, d1)
                if not os.path.isdir(p1):
                    continue
                # Check level 1: X:\something\oxide\plugins
                candidate = os.path.join(p1, "oxide", "plugins")
                if os.path.isdir(candidate):
                    found.append(candidate)
                    continue
                # Check level 2: X:\something\server\oxide\plugins
                try:
                    for d2 in os.listdir(p1):
                        p2 = os.path.join(p1, d2)
                        if not os.path.isdir(p2):
                            continue
                        candidate = os.path.join(p2, "oxide", "plugins")
                        if os.path.isdir(candidate):
                            found.append(candidate)
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass

    return jsonify({"ok": True, "paths": found})

@app.route("/api/setup", methods=["POST"])
def api_setup():
    if is_setup_done():
        return jsonify({"ok": False, "msg": "Already configured"})
    data = request.json
    pin = data.get("pin", "")
    plugin_dir = data.get("plugin_dir", "")
    if not pin or len(pin) != 4:
        return jsonify({"ok": False, "msg": "PIN must be 4 digits"})
    if not plugin_dir:
        return jsonify({"ok": False, "msg": "Plugin directory is required"})
    config = load_config()
    config["pin"] = pin
    config["plugin_dir"] = plugin_dir
    config["archive_dir"] = data.get("archive_dir", "")
    config["rcon_password"] = data.get("rcon_password", "")
    config["rcon_port"] = data.get("rcon_port", 28016)
    config["setup_complete"] = True
    save_config(config)
    return jsonify({"ok": True})

@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_setup_done():
        return redirect("/setup")
    error = None
    if request.method == "POST":
        config = load_config()
        if request.form.get("pin") == config["pin"]:
            session["authed"] = True
            return redirect("/")
        error = "Incorrect PIN."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/api/settings", methods=["GET"])
def get_settings():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    return jsonify({
        "ok": True,
        "plugin_dir": config.get("plugin_dir", ""),
        "archive_dir": config.get("archive_dir", ""),
        "rcon_host": config.get("rcon_host", ""),
        "rcon_port": config.get("rcon_port", 28016),
        "rcon_password": config.get("rcon_password", ""),
        "update_url": config.get("update_url", "")
    })

@app.route("/api/settings", methods=["POST"])
def save_settings():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    config = load_config()
    if "pin" in data and data["pin"]:
        if len(data["pin"]) != 4 or not data["pin"].isdigit():
            return jsonify({"ok": False, "msg": "PIN must be 4 digits"})
        config["pin"] = data["pin"]
    if "plugin_dir" in data:
        config["plugin_dir"] = data["plugin_dir"]
    if "archive_dir" in data:
        config["archive_dir"] = data["archive_dir"]
    if "rcon_host" in data:
        config["rcon_host"] = data["rcon_host"]
    if "rcon_port" in data:
        config["rcon_port"] = int(data["rcon_port"]) if data["rcon_port"] else 28016
    if "rcon_password" in data:
        config["rcon_password"] = data["rcon_password"]
    if "update_url" in data:
        config["update_url"] = data["update_url"]
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/scan", methods=["GET"])
def scan():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    plugin_dir = config.get("plugin_dir", "C:\\rustserver\\oxide\\plugins")
    found = scan_plugins(plugin_dir)
    tracked_names = {p["name"] for p in config["plugins"]}
    tracked_groups = {p["name"]: p.get("group", "") for p in config["plugins"]}
    for p in found:
        p["tracked"] = p["name"] in tracked_names
        p["group"] = tracked_groups.get(p["name"], "")
    return jsonify({"ok": True, "plugins": found, "plugin_dir": plugin_dir})

@app.route("/api/manage/save", methods=["POST"])
def manage_save():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    config = load_config()
    selected = data.get("plugins", [])
    groups = data.get("groups", [])
    existing = {p["name"]: p for p in config["plugins"]}
    new_list = []
    for p in selected:
        if p["name"] in existing:
            entry = existing[p["name"]]
            entry["group"] = p.get("group", entry.get("group", ""))
        else:
            entry = {
                "name": p["name"],
                "author": p.get("author", ""),
                "file": p.get("file", ""),
                "group": p.get("group", ""),
                "webhook": "",
                "channel": "",
                "project_url": "",
                "version": p.get("version", [1, 0, 0])
            }
        new_list.append(entry)
    config["plugins"] = new_list
    config["groups"] = groups
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/log", methods=["POST"])
def log_update():
    if not session.get("authed"):
        return jsonify({"ok": False, "msg": "Unauthorized"}), 401
    data = request.json
    config = load_config()
    plugin_name = data["plugin"]
    version_str = data["version"]
    update_type = data["type"]
    notes = data["notes"]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plugin_dir = config.get("plugin_dir", "C:\\rustserver\\oxide\\plugins")
    webhook = ""
    plugin_file = ""
    version_list = [int(x) for x in version_str.strip("v").split(".")]

    for p in config["plugins"]:
        if p["name"] == plugin_name:
            p["version"] = version_list
            webhook = p.get("webhook", "")
            plugin_file = p.get("file", "")
            break

    if plugin_file:
        filepath = os.path.join(plugin_dir, plugin_file)
        code = data.get("code")
        if code:
            code = code.replace("\r\n", "\n")
            try:
                with open(filepath, "w", encoding="utf-8", newline="\n") as f:
                    f.write(code)
            except Exception:
                pass
        update_version_in_file(filepath, version_list)
        snapshot_plugin(plugin_dir, plugin_file, plugin_name, version_list, update_type, notes, ts)

    save_config(config)

    log_entry = f"\n[{plugin_name}] {version_str} - {update_type} - {ts}\n{notes}\n{'-'*60}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)

    discord_ok = True
    if webhook:
        msg = f"**[{plugin_name}] {version_str}** — {update_type}\n{notes}\n*{ts}*"
        try:
            r = requests.post(webhook, json={"content": msg}, timeout=5)
            discord_ok = r.ok
        except Exception:
            discord_ok = False

    next_version = version_list[:]
    next_version[2] += 1

    return jsonify({"ok": True, "discord": discord_ok, "next_version": next_version})

@app.route("/api/webhook", methods=["POST"])
def save_webhook():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    config = load_config()
    for p in config["plugins"]:
        if p["name"] == data["plugin"]:
            p["webhook"] = data["webhook"]
            p["channel"] = data.get("channel", "")
            p["project_url"] = data.get("project_url", "")
            break
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/webhook/test", methods=["POST"])
def test_webhook():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    try:
        r = requests.post(data["webhook"], json={"content": f"**[{data['plugin']}]** Webhook test — OxLog connected."}, timeout=5)
        return jsonify({"ok": r.ok})
    except Exception:
        return jsonify({"ok": False})

@app.route("/api/open", methods=["POST"])
def open_in_vs():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    config = load_config()
    plugin_dir = config.get("plugin_dir", "C:\\rustserver\\oxide\\plugins")
    for p in config["plugins"]:
        if p["name"] == data["plugin"]:
            filepath = os.path.join(plugin_dir, p["file"])
            if os.path.exists(filepath):
                subprocess.Popen([
                    r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\devenv.exe",
                    filepath
                ])
                return jsonify({"ok": True})
            return jsonify({"ok": False, "msg": "File not found"})
    return jsonify({"ok": False, "msg": "Plugin not found"})

@app.route("/api/open/snapshot", methods=["POST"])
def open_snapshot():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    folder = data.get("folder", "")
    abs_folder = os.path.abspath(folder)
    if os.path.isdir(abs_folder):
        subprocess.Popen(["explorer", abs_folder])
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "Folder not found"})

@app.route("/api/revert", methods=["POST"])
def revert():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    plugin_name = data["plugin"]
    version_str = data["version"]
    folder = data["folder"]
    config = load_config()
    plugin_dir = config.get("plugin_dir", "C:\\rustserver\\oxide\\plugins")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    plugin_entry = None
    for p in config["plugins"]:
        if p["name"] == plugin_name:
            plugin_entry = p
            break
    if not plugin_entry:
        return jsonify({"ok": False, "msg": "Plugin not found in config"})

    plugin_file = plugin_entry.get("file", "")
    if not plugin_file:
        return jsonify({"ok": False, "msg": "No file associated with plugin"})

    live_path = os.path.join(plugin_dir, plugin_file)
    abs_folder = os.path.abspath(folder)
    snapshot_src = os.path.join(abs_folder, plugin_file)

    if not os.path.exists(snapshot_src):
        return jsonify({"ok": False, "msg": "Snapshot file not found"})

    # Snapshot current live file before reverting
    pre_version = plugin_entry.get("version", [0, 0, 0])
    snapshot_plugin(plugin_dir, plugin_file, plugin_name, pre_version, "Pre-Revert", "Auto-snapshot before revert to " + version_str, ts)

    # Restore snapshot to live
    oxide_root = os.path.dirname(plugin_dir)
    try:
        shutil.copy2(snapshot_src, live_path)
        # Restore config files
        snapshot_config = os.path.join(abs_folder, "config")
        if os.path.isdir(snapshot_config):
            config_dir = os.path.join(oxide_root, "config")
            for fname in os.listdir(snapshot_config):
                src = os.path.join(snapshot_config, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(config_dir, fname))
        # Restore data files
        snapshot_data = os.path.join(abs_folder, "data")
        if os.path.isdir(snapshot_data):
            data_dir = os.path.join(oxide_root, "data")
            for root, dirs, files_list in os.walk(snapshot_data):
                for fname in files_list:
                    src = os.path.join(root, fname)
                    rel = os.path.relpath(src, snapshot_data)
                    dest = os.path.join(data_dir, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)
    except Exception as e:
        return jsonify({"ok": False, "msg": "Failed to restore: " + str(e)})

    # Parse version from restored file
    info = parse_plugin_info(live_path)
    version_list = info["version"] if info else [int(x) for x in version_str.strip("v").split(".")]
    plugin_entry["version"] = version_list

    # Snapshot the reverted state
    snapshot_plugin(plugin_dir, plugin_file, plugin_name, version_list, "Revert", "Reverted to " + version_str, ts)

    save_config(config)

    # Log
    log_entry = f"\n[{plugin_name}] {version_str} - Revert - {ts}\nReverted to {version_str}\n{'-'*60}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)

    # Discord
    webhook = plugin_entry.get("webhook", "")
    if webhook:
        msg = f"**[{plugin_name}] {version_str}** — Revert\nReverted to {version_str}\n*{ts}*"
        try:
            requests.post(webhook, json={"content": msg}, timeout=5)
        except Exception:
            pass

    return jsonify({"ok": True, "version": version_list})

@app.route("/api/recent", methods=["GET"])
def recent():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    last_updated = {}
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("["):
                    parts = line.strip().split(" - ")
                    if len(parts) >= 3:
                        raw = parts[0].strip("[]")
                        name_ver = raw.split("] ", 1)
                        name = name_ver[0] if len(name_ver) > 1 else raw
                        ts = parts[2].strip()
                        last_updated[name] = ts
    return jsonify({"ok": True, "last_updated": last_updated})

@app.route("/api/history")
def history():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    plugin = request.args.get("plugin", "")
    entries = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("[") and (not plugin or f"[{plugin}]" in line):
                    parts = line.strip().split(" - ")
                    if len(parts) >= 3:
                        raw = parts[0].strip("[]")
                        name_ver = raw.split("] ", 1)
                        pname = name_ver[0] if len(name_ver) > 1 else raw
                        ver = name_ver[1] if len(name_ver) > 1 else ""
                        update_type = parts[1]
                        ts = parts[2]
                        safe_ts = ts.replace(":", "-").replace(" ", "_")
                        folder = os.path.join(get_versions_dir(), pname, f"{ver} - {update_type} - {safe_ts}")
                        notes_content = ""
                        notes_file = os.path.join(folder, "notes.txt")
                        if os.path.exists(notes_file):
                            try:
                                with open(notes_file, "r", encoding="utf-8") as nf:
                                    lines = nf.read().split("\n\n", 1)
                                    notes_content = lines[1] if len(lines) > 1 else ""
                            except Exception:
                                pass
                        entries.append({
                            "plugin": pname,
                            "version": ver,
                            "type": update_type,
                            "ts": ts,
                            "folder": folder,
                            "notes": notes_content
                        })
    return jsonify(list(reversed(entries)))

@app.route("/api/search", methods=["POST"])
def search_plugins():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    query = data.get("query", "").strip()
    if not query or len(query) < 2:
        return jsonify({"ok": False, "msg": "Query too short"})
    config = load_config()
    plugin_dir = config.get("plugin_dir", "")
    results = []
    total_matches = 0
    query_lower = query.lower()
    for p in config["plugins"]:
        pfile = p.get("file", "")
        if not pfile:
            continue
        filepath = os.path.join(plugin_dir, pfile)
        if not os.path.exists(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            matches = []
            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    matches.append({
                        "line": i + 1,
                        "text": line.rstrip()[:300]
                    })
                    total_matches += 1
            if matches:
                results.append({
                    "plugin": p["name"],
                    "file": pfile,
                    "matches": matches[:50]  # cap per file
                })
        except Exception:
            continue
    return jsonify({"ok": True, "results": results, "total": total_matches,
                    "files": len(results)})

@app.route("/api/plugin/files", methods=["POST"])
def get_plugin_files():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    config = load_config()
    plugin_dir = config.get("plugin_dir", "")
    plugin_name = data.get("plugin", "")
    oxide_root = os.path.dirname(plugin_dir)  # oxide/ parent
    config_dir = os.path.join(oxide_root, "config")
    data_dir = os.path.join(oxide_root, "data")

    plugin_entry = None
    for p in config["plugins"]:
        if p["name"] == plugin_name:
            plugin_entry = p
            break
    if not plugin_entry:
        return jsonify({"ok": False, "msg": "Plugin not found"})

    files = {"plugin": [], "config": [], "data": []}

    # Main .cs file
    cs_file = plugin_entry.get("file", "")
    if cs_file:
        cs_path = os.path.join(plugin_dir, cs_file)
        if os.path.exists(cs_path):
            files["plugin"].append({"name": cs_file, "path": cs_path, "type": "cs"})

    # Config file
    config_file = plugin_name + ".json"
    config_path = os.path.join(config_dir, config_file)
    if os.path.exists(config_path):
        files["config"].append({"name": config_file, "path": config_path, "type": "json"})

    # Data files — check for plugin name matches
    if os.path.isdir(data_dir):
        # Direct file match
        data_json = plugin_name + ".json"
        data_path = os.path.join(data_dir, data_json)
        if os.path.exists(data_path):
            files["data"].append({"name": data_json, "path": data_path, "type": "json"})
        # Subfolder match
        data_subfolder = os.path.join(data_dir, plugin_name)
        if os.path.isdir(data_subfolder):
            try:
                for fname in sorted(os.listdir(data_subfolder)):
                    fpath = os.path.join(data_subfolder, fname)
                    if os.path.isfile(fpath):
                        files["data"].append({
                            "name": plugin_name + "/" + fname,
                            "path": fpath,
                            "type": "json" if fname.endswith(".json") else "data"
                        })
            except PermissionError:
                pass
        # Also parse .cs for DataFileSystem references
        if cs_file:
            try:
                cs_path = os.path.join(plugin_dir, cs_file)
                with open(cs_path, "r", encoding="utf-8", errors="ignore") as f:
                    source = f.read()
                # Match GetFile("name"), GetDatafile("name"), ReadObject("name")
                refs = set(re.findall(r'(?:GetFile|GetDatafile|ReadObject|WriteObject)\s*(?:<[^>]*>)?\s*\(\s*"([^"]+)"', source))
                for ref in sorted(refs):
                    ref_path = os.path.join(data_dir, ref + ".json") if not ref.endswith(".json") else os.path.join(data_dir, ref)
                    ref_name = ref + ".json" if not ref.endswith(".json") else ref
                    if os.path.exists(ref_path):
                        # Avoid duplicates
                        existing = {f["path"] for f in files["data"]}
                        if ref_path not in existing:
                            files["data"].append({"name": ref_name, "path": ref_path, "type": "json"})
            except Exception:
                pass

    return jsonify({"ok": True, "files": files, "dirs": {
        "plugin": plugin_dir,
        "config": config_dir,
        "data": data_dir
    }})

@app.route("/api/plugin/code", methods=["POST"])
def get_plugin_code():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    config = load_config()
    plugin_dir = config.get("plugin_dir", "")
    plugin_name = data.get("plugin", "")
    filepath = data.get("filepath", "")

    if filepath:
        # Direct file path mode
        abs_path = os.path.abspath(filepath)
        oxide_root = os.path.dirname(plugin_dir)
        # Security: only allow files under the oxide root
        if not abs_path.startswith(os.path.abspath(oxide_root)):
            return jsonify({"ok": False, "msg": "Access denied"})
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    code = f.read()
                return jsonify({"ok": True, "code": code, "file": os.path.basename(abs_path)})
            except Exception as e:
                return jsonify({"ok": False, "msg": str(e)})
        return jsonify({"ok": False, "msg": "File not found"})

    # Legacy: load by plugin name (main .cs file)
    for p in config["plugins"]:
        if p["name"] == plugin_name:
            fp = os.path.join(plugin_dir, p.get("file", ""))
            if os.path.exists(fp):
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        code = f.read()
                    return jsonify({"ok": True, "code": code, "file": p.get("file", "")})
                except Exception as e:
                    return jsonify({"ok": False, "msg": str(e)})
            return jsonify({"ok": False, "msg": "File not found"})
    return jsonify({"ok": False, "msg": "Plugin not found"})

@app.route("/api/plugin/save", methods=["POST"])
def save_plugin_code():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    config = load_config()
    plugin_dir = config.get("plugin_dir", "")
    plugin_name = data.get("plugin", "")
    code = data.get("code", "")
    filepath = data.get("filepath", "")
    if not code:
        return jsonify({"ok": False, "msg": "No code provided"})

    oxide_root = os.path.dirname(plugin_dir)

    if filepath:
        abs_path = os.path.abspath(filepath)
        if not abs_path.startswith(os.path.abspath(oxide_root)):
            return jsonify({"ok": False, "msg": "Access denied"})
        try:
            code = code.replace("\r\n", "\n")
            with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(code)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    for p in config["plugins"]:
        if p["name"] == plugin_name:
            fp = os.path.join(plugin_dir, p.get("file", ""))
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            snapshot_plugin(plugin_dir, p.get("file", ""), plugin_name,
                p.get("version", [0,0,0]), "Pre-Edit", "Auto-snapshot before editor save", ts)
            try:
                code = code.replace("\r\n", "\n")
                with open(fp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(code)
                return jsonify({"ok": True})
            except Exception as e:
                return jsonify({"ok": False, "msg": str(e)})
    return jsonify({"ok": False, "msg": "Plugin not found"})

@app.route("/api/diff", methods=["POST"])
def diff_view():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    plugin_name = data.get("plugin", "")
    folder = data.get("folder", "")
    mode = data.get("mode", "live")  # "live" or "previous"
    config = load_config()
    plugin_dir = config.get("plugin_dir", "")

    plugin_file = ""
    for p in config["plugins"]:
        if p["name"] == plugin_name:
            plugin_file = p.get("file", "")
            break
    if not plugin_file:
        return jsonify({"ok": False, "msg": "Plugin not found"})

    abs_folder = os.path.abspath(folder)
    snapshot_path = os.path.join(abs_folder, plugin_file)
    if not os.path.exists(snapshot_path):
        return jsonify({"ok": False, "msg": "Snapshot file not found"})

    try:
        with open(snapshot_path, "r", encoding="utf-8", errors="ignore") as f:
            snapshot_lines = f.read().splitlines()
    except Exception:
        return jsonify({"ok": False, "msg": "Could not read snapshot"})

    if mode == "live":
        live_path = os.path.join(plugin_dir, plugin_file)
        if not os.path.exists(live_path):
            return jsonify({"ok": False, "msg": "Live file not found"})
        try:
            with open(live_path, "r", encoding="utf-8", errors="ignore") as f:
                compare_lines = f.read().splitlines()
        except Exception:
            return jsonify({"ok": False, "msg": "Could not read live file"})
        label_a = "Snapshot"
        label_b = "Live"
    else:
        # Find previous snapshot
        plugin_versions_dir = os.path.join(get_versions_dir(), plugin_name)
        if not os.path.isdir(plugin_versions_dir):
            return jsonify({"ok": False, "msg": "No version history"})
        folders = sorted(os.listdir(plugin_versions_dir))
        current_folder_name = os.path.basename(abs_folder)
        idx = -1
        for i, fn in enumerate(folders):
            if fn == current_folder_name:
                idx = i
                break
        if idx <= 0:
            return jsonify({"ok": False, "msg": "No previous version to compare"})
        prev_folder = os.path.join(plugin_versions_dir, folders[idx - 1])
        prev_path = os.path.join(prev_folder, plugin_file)
        if not os.path.exists(prev_path):
            return jsonify({"ok": False, "msg": "Previous snapshot file not found"})
        try:
            with open(prev_path, "r", encoding="utf-8", errors="ignore") as f:
                compare_lines = f.read().splitlines()
        except Exception:
            return jsonify({"ok": False, "msg": "Could not read previous snapshot"})
        label_a = "Previous"
        label_b = "This Version"

    diff = list(difflib.unified_diff(compare_lines, snapshot_lines,
        fromfile=label_a, tofile=label_b, lineterm=""))
    return jsonify({"ok": True, "diff": diff, "label_a": label_a, "label_b": label_b})

@app.route("/api/diff/staged", methods=["POST"])
def diff_staged():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    plugin_name = data.get("plugin", "")
    staged_code = data.get("code", "")
    if not staged_code:
        return jsonify({"ok": False, "msg": "No staged code"})
    config = load_config()
    plugin_dir = config.get("plugin_dir", "")
    plugin_file = ""
    for p in config["plugins"]:
        if p["name"] == plugin_name:
            plugin_file = p.get("file", "")
            break
    if not plugin_file:
        return jsonify({"ok": False, "msg": "Plugin not found"})
    live_path = os.path.join(plugin_dir, plugin_file)
    if not os.path.exists(live_path):
        return jsonify({"ok": False, "msg": "Live file not found"})
    try:
        with open(live_path, "r", encoding="utf-8", errors="ignore") as f:
            live_lines = f.read().splitlines()
    except Exception:
        return jsonify({"ok": False, "msg": "Could not read live file"})
    staged_lines = staged_code.splitlines()
    diff = list(difflib.unified_diff(live_lines, staged_lines,
        fromfile="Live", tofile="Staged", lineterm=""))
    return jsonify({"ok": True, "diff": diff, "label_a": "Live", "label_b": "Staged"})

@app.route("/api/groups", methods=["GET"])
def get_groups():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    return jsonify({"ok": True, "groups": config.get("groups", [])})

@app.route("/api/rcon/config", methods=["GET"])
def rcon_config():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    password = config.get("rcon_password", "")
    if not password:
        return jsonify({"ok": False, "msg": "RCON not configured"})
    return jsonify({
        "ok": True,
        "host": config.get("rcon_host", ""),
        "port": config.get("rcon_port", 28016),
        "password": password
    })

@app.route("/api/archive", methods=["POST"])
def archive():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    archive_dir = config.get("archive_dir", "backup")
    if not archive_dir:
        archive_dir = "backup"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"OxLog_backup_{ts}"
    backup_path = os.path.join(archive_dir, backup_name)
    try:
        os.makedirs(backup_path, exist_ok=True)
        for item in [CONFIG_FILE, LOG_FILE, "OxLog.py"]:
            if os.path.exists(item):
                shutil.copy2(item, backup_path)
        if os.path.isdir("templates"):
            shutil.copytree("templates", os.path.join(backup_path, "templates"))
        if os.path.isdir(get_versions_dir()):
            shutil.copytree(get_versions_dir(), os.path.join(backup_path, "versions"))
        return jsonify({"ok": True, "path": backup_path})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/tutorial/complete", methods=["POST"])
def tutorial_complete():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    config["tutorial_complete"] = True
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/oxide/snapshot", methods=["POST"])
def oxide_snapshot():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    plugin_dir = config.get("plugin_dir", "")
    if not plugin_dir:
        return jsonify({"ok": False, "msg": "Plugin directory not configured"})
    oxide_root = os.path.dirname(plugin_dir)
    if not os.path.isdir(oxide_root):
        return jsonify({"ok": False, "msg": "Oxide directory not found: " + oxide_root})
    archive_dir = config.get("archive_dir", "backup")
    if not archive_dir:
        archive_dir = "backup"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_name = f"oxide_snapshot_{ts}"
    snapshot_path = os.path.join(archive_dir, snapshot_name)
    try:
        shutil.copytree(oxide_root, snapshot_path)
        total = 0
        for dirpath, dirnames, filenames in os.walk(snapshot_path):
            for f in filenames:
                total += os.path.getsize(os.path.join(dirpath, f))
        if total > 1073741824:
            size_str = f"{total / 1073741824:.1f} GB"
        elif total > 1048576:
            size_str = f"{total / 1048576:.1f} MB"
        else:
            size_str = f"{total / 1024:.1f} KB"
        return jsonify({"ok": True, "path": snapshot_path, "size": size_str})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/update/check", methods=["GET"])
def update_check():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    config = load_config()
    update_url = config.get("update_url", "") or UPDATE_URL
    if not update_url:
        return jsonify({"ok": False, "msg": "No update URL configured. Set it in Settings."})
    try:
        import time
        cache_bust = update_url + ("&" if "?" in update_url else "?") + "t=" + str(int(time.time()))
        r = requests.get(cache_bust, timeout=10)
        if not r.ok:
            return jsonify({"ok": False, "msg": f"Failed to check: HTTP {r.status_code}"})
        data = r.json()
        remote_ver = data.get("version", "")
        notes = data.get("notes", "")
        if not remote_ver:
            return jsonify({"ok": False, "msg": "Invalid version info from server"})
        def parse_ver(v):
            return [int(x) for x in v.strip("v").split(".")]
        is_newer = parse_ver(remote_ver) > parse_ver(OXLOG_VERSION)
        return jsonify({
            "ok": True,
            "current": OXLOG_VERSION,
            "latest": remote_ver,
            "newer": is_newer,
            "base_url": data.get("base_url", ""),
            "files": data.get("files", []),
            "notes": notes
        })
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/update/apply", methods=["POST"])
def update_apply():
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    data = request.json
    base_url = data.get("base_url", "")
    files = data.get("files", [])
    if not base_url or not files:
        return jsonify({"ok": False, "msg": "No update files specified"})
    app_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        # Back up current app files
        backup_dir = os.path.join(app_dir, "backup")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_update = os.path.join(backup_dir, f"pre_update_{ts}")
        os.makedirs(pre_update, exist_ok=True)
        oxlog_path = os.path.join(app_dir, "OxLog.py")
        templates_path = os.path.join(app_dir, "templates")
        if os.path.exists(oxlog_path):
            shutil.copy2(oxlog_path, pre_update)
        if os.path.isdir(templates_path):
            shutil.copytree(templates_path, os.path.join(pre_update, "templates"))

        # Download each file to staging
        staging = os.path.join(backup_dir, "pending_update")
        if os.path.isdir(staging):
            shutil.rmtree(staging)
        os.makedirs(staging, exist_ok=True)

        for fname in files:
            url = base_url.rstrip("/") + "/" + fname
            r = requests.get(url, timeout=30)
            if not r.ok:
                return jsonify({"ok": False, "msg": f"Failed to download {fname}: HTTP {r.status_code}"})
            dest = os.path.join(staging, fname)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(r.content)

        return jsonify({"ok": True, "backup": pre_update,
                        "msg": "Update staged. Restarting OxLog..."})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/update/restart", methods=["POST"])
def update_restart():
    """Shut down the server so start.bat restarts it with the new code"""
    if not session.get("authed"):
        return jsonify({"ok": False}), 401
    import threading
    def do_exit():
        import time
        time.sleep(1)
        os._exit(0)
    threading.Thread(target=do_exit, daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    # Apply pending update if one was staged
    app_dir = os.path.dirname(os.path.abspath(__file__))
    pending = os.path.join(app_dir, "backup", "pending_update")
    if os.path.isdir(pending):
        print("  Applying pending update...")
        try:
            for root, dirs, files in os.walk(pending):
                for f in files:
                    src = os.path.join(root, f)
                    rel = os.path.relpath(src, pending)
                    dest = os.path.join(app_dir, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)
            shutil.rmtree(pending)
            # Read version from the new OxLog.py and update in memory
            new_oxlog = os.path.join(app_dir, "OxLog.py")
            try:
                with open(new_oxlog, "r", encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r'OXLOG_VERSION\s*=\s*"([^"]+)"', line)
                        if m:
                            globals()["OXLOG_VERSION"] = m.group(1)
                            break
            except Exception:
                pass
            # Write flag so the UI shows a splash
            with open(os.path.join(app_dir, ".updated"), "w") as f:
                f.write("ok")
            print("  Update applied successfully.")
        except Exception as e:
            print(f"  Update failed: {e}")

    @app.errorhandler(Exception)
    def handle_exception(e):
        return jsonify({"ok": False, "msg": str(e)}), 500

    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "YOUR_IP"

    print(f"\n  OxLog v{OXLOG_VERSION} running on:")
    print(f"    http://localhost:5000")
    print(f"    http://{local_ip}:5000\n")

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=5000, threads=4)
    except ImportError:
        print("  (Waitress not found, using Flask dev server)")
        app.run(host="0.0.0.0", port=5000, debug=False)
