#!/usr/bin/env python3
"""Optional: writes ~/.config/phonecall/fleet.json (fleet status and quotas) for the phone's «Fleet» widget.

The server serves it as is on GET /fleet (phone token required). Run it every 15 minutes from launchd if you
want the widget. It only reads what already exists on this Mac: the firstmate backlog (PHONECALL_FM_HOME) and
quota-axi (PHONECALL_QUOTA, optional); a source that fails shows as «no-reading» instead of made-up data.
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BACKLOG = Path(os.environ.get("PHONECALL_FM_HOME") or Path.home() / "firstmate") / "data/backlog.md"
OUTPUT = Path(os.environ.get("PHONECALL_CONFIG") or Path.home() / ".config/phonecall") / "fleet.json"
QUOTA = Path(os.environ.get("PHONECALL_QUOTA") or "/opt/homebrew/bin/quota-axi")
NAMES = {"claude": "Claude Code", "codex": "Codex", "grok": "Grok"}
ITEM = re.compile(r"^- \[[ x]\] (\S+) - (.+?)(?: \((?:repo|kind|since|hold):.*)?$")


def backlog() -> dict:
    """Counts the backlog work: in flight, waiting on the human (hold) and queued."""
    section, running, waiting, queued = "", [], [], []
    try:
        lines = BACKLOG.read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        m = ITEM.match(line)
        if not m or section not in ("in flight", "queued"):
            continue
        title = re.sub(r"\s*\(.*$", "", m.group(2)).strip() or m.group(1)
        if section == "in flight":
            running.append(title)
        elif "(hold:" in line:
            waiting.append(title)
        else:
            queued.append(title)
    return {"running": len(running), "waiting": len(waiting), "queued": len(queued),
            "waiting_titles": waiting[:3], "running_titles": running[:3]}


def quotas() -> list[dict]:
    try:
        raw = subprocess.run([str(QUOTA), "--provider", "claude,codex,grok", "--json", "--no-credential-refresh"],
                             capture_output=True, text=True, timeout=60).stdout
        providers = {p["provider"]: p for p in json.loads(raw)["providers"]}
    except Exception:  # noqa: BLE001 - any failure shows as «no-reading»
        providers = {}
    out = []
    for key, name in NAMES.items():
        p = providers.get(key, {})
        windows = p.get("windows") or []
        if p.get("state", {}).get("status") == "fresh" and windows:
            worst = min(windows, key=lambda w: w.get("percentRemaining", 100))
            out.append({"name": name, "state": "ok", "remaining": round(worst["percentRemaining"]),
                        "resets": worst.get("resetsAt")})
        else:
            out.append({"name": name, "state": "no-reading"})
    return out


def main() -> None:
    data = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fleet": backlog(), "quotas": quotas()}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(f".{int(time.time())}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False))
    temporary.replace(OUTPUT)


if __name__ == "__main__":
    main()
