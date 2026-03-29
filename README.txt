
  OxLog — Oxide Plugin Changelog
  ================================

  QUICK START
  -----------
  1. Run install.bat  (installs Python + dependencies)
  2. Run start.bat    (launches OxLog)
  3. Open http://localhost:5000 in your browser
  4. Complete the setup wizard (PIN, paths, optional RCON)

  That's it. OxLog is running.


  WHAT IT DOES
  ------------
  - Tracks Oxide/Rust plugin versions with a changelog
  - Logs updates, snapshots code before/after changes
  - Pushes changelogs to Discord via webhooks
  - Live RCON console with filtering
  - Diff viewer between versions
  - One-click revert to any previous version
  - Paste code from Claude/editor directly into live plugin files


  FEATURES
  --------
  Log Update    Version bump, update type, notes, paste code, push to Discord
  Webhook       Per-plugin Discord webhook config, test button
  History       Full changelog with diff, revert, snapshot browser
  Console       Live RCON feed, filter by plugin/errors, load/unload/reload
  Settings      PIN, paths, RCON config
  Archive       Full backup to timestamped folder


  FILE STRUCTURE
  --------------
  OxLog/
  ├── OxLog.py               Main server
  ├── config.json             Settings + plugin list (auto-created on setup)
  ├── plugin_changelog.txt    Text log of all updates
  ├── install.bat             One-time installer
  ├── start.bat               Launch script
  ├── templates/
  │   ├── index.html          Main UI
  │   ├── login.html          PIN login screen
  │   └── setup.html          First-run wizard
  ├── versions/               Per-plugin snapshots
  └── archive/                Full backups


  CONFIG.JSON
  -----------
  Created automatically by the setup wizard. Key fields:

    pin              4-digit login PIN
    plugin_dir       Path to oxide/plugins (e.g. C:\rustserver\oxide\plugins)
    archive_dir      Where backups go (default: archive/)
    rcon_password    Your server's +rcon.password value
    rcon_port        RCON WebSocket port (default: 28016)
    rcon_host        Leave blank to auto-detect from browser


  NETWORK ACCESS
  --------------
  OxLog runs on port 5000. Access it via:
    - localhost:5000           (on the server itself)
    - YOUR_SERVER_IP:5000      (from LAN)
    - Tailscale IP:5000        (remote access via Tailscale)

  If using Tailscale and you want clipboard paste to work,
  either access via localhost or set the Chrome flag:
    chrome://flags/#unsafely-treat-insecure-origin-as-secure
    Add: http://YOUR_TAILSCALE_IP:5000


  AUTO-START ON BOOT
  ------------------
  To start OxLog automatically when Windows boots:

  1. Press Win+R, type: shell:startup
  2. Create a shortcut to start.bat in that folder

  Or use Task Scheduler:
  1. Open Task Scheduler
  2. Create Basic Task > "OxLog"
  3. Trigger: "When the computer starts"
  4. Action: Start a program
     Program: pythonw.exe
     Arguments: OxLog.py
     Start in: (your OxLog folder path)


  REQUIREMENTS
  ------------
  - Windows 10/11
  - Python 3.10+ (installed by install.bat)
  - Flask, Requests, Waitress (installed by install.bat)

