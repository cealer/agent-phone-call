#!/usr/bin/env python3
"""Optional: writes ~/.config/llamada/flota.json (fleet status and quotas) for the phone's «Flota» widget.

The server serves it as is on GET /flota (phone token required). Run it every 15 minutes from launchd if you
want the widget. It only reads what already exists on this Mac: the firstmate backlog (LLAMADA_FM_HOME) and
quota-axi (LLAMADA_QUOTA, optional); a source that fails shows as «sin lectura» (no reading) instead of made-up data.
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BACKLOG = Path(os.environ.get("LLAMADA_FM_HOME") or Path.home() / "firstmate") / "data/backlog.md"
SALIDA = Path(os.environ.get("LLAMADA_CONFIG") or Path.home() / ".config/llamada") / "flota.json"
QUOTA = Path(os.environ.get("LLAMADA_QUOTA") or "/opt/homebrew/bin/quota-axi")
NOMBRES = {"claude": "Claude Code", "codex": "Codex", "grok": "Grok"}
ITEM = re.compile(r"^- \[[ x]\] (\S+) - (.+?)(?: \((?:repo|kind|since|hold):.*)?$")


def backlog() -> dict:
    """Counts the backlog work: in flight, waiting on the human (hold) and queued."""
    seccion, marcha, espera, cola = "", [], [], []
    try:
        lineas = BACKLOG.read_text().splitlines()
    except OSError:
        lineas = []
    for linea in lineas:
        if linea.startswith("## "):
            seccion = linea[3:].strip().lower()
            continue
        m = ITEM.match(linea)
        if not m or seccion not in ("in flight", "queued"):
            continue
        titulo = re.sub(r"\s*\(.*$", "", m.group(2)).strip() or m.group(1)
        if seccion == "in flight":
            marcha.append(titulo)
        elif "(hold:" in linea:
            espera.append(titulo)
        else:
            cola.append(titulo)
    return {"marcha": len(marcha), "espera": len(espera), "cola": len(cola),
            "espera_titulos": espera[:3], "marcha_titulos": marcha[:3]}


def cuotas() -> list[dict]:
    try:
        crudo = subprocess.run([str(QUOTA), "--provider", "claude,codex,grok", "--json", "--no-credential-refresh"],
                               capture_output=True, text=True, timeout=60).stdout
        proveedores = {p["provider"]: p for p in json.loads(crudo)["providers"]}
    except Exception:  # noqa: BLE001 - any failure shows as «sin lectura»
        proveedores = {}
    salida = []
    for clave, nombre in NOMBRES.items():
        p = proveedores.get(clave, {})
        ventanas = p.get("windows") or []
        if p.get("state", {}).get("status") == "fresh" and ventanas:
            peor = min(ventanas, key=lambda w: w.get("percentRemaining", 100))
            salida.append({"nombre": nombre, "estado": "ok", "restante": round(peor["percentRemaining"]),
                           "reinicia": peor.get("resetsAt")})
        else:
            salida.append({"nombre": nombre, "estado": "sin-lectura"})
    return salida


def main() -> None:
    datos = {"actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "flota": backlog(), "cuotas": cuotas()}
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    temporal = SALIDA.with_suffix(f".{int(time.time())}.tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False))
    temporal.replace(SALIDA)


if __name__ == "__main__":
    main()
