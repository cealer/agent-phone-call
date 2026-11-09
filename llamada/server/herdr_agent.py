"""The agents a call can talk to, reached through Herdr's CLI (local server socket)."""
import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

HERDR = os.environ.get("HERDR_BIN") or shutil.which("herdr") or os.path.expanduser("~/.local/bin/herdr")
# Secondmates are steered through their firstmate inbox (fm-send), never typed into their pane.
FM_SEND = os.environ.get("LLAMADA_FM_SEND") or os.path.expanduser("~/.local/bin/fm-send-vps")
CONFIG_FILE = Path(os.environ.get("LLAMADA_CONFIG", Path.home() / ".config" / "llamada")) / "config.json"


class HerdrError(RuntimeError):
    pass


def _contact(target: str) -> dict:
    """The configured contact whose agent is this target ({} when none)."""
    try:
        contacts = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("contacts", [])
    except (OSError, ValueError):
        return {}
    return next((c for c in contacts if c.get("agent") == target), {})


async def _exec(binary: str, *args: str, timeout: float = 15) -> bytes:
    process = await asyncio.create_subprocess_exec(
        binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        raise HerdrError(f"{os.path.basename(binary)} {args[0]} timed out")
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise HerdrError(detail)
    return stdout


async def _run(*args: str, herdr: str = HERDR, timeout: float = 15) -> dict:
    return json.loads(await _exec(os.path.expanduser(herdr), *args, timeout=timeout) or b"{}")


async def status(target: str) -> str:
    """idle | working | blocked | done | unknown, or 'offline' when no such live agent."""
    try:
        result = await _run("agent", "get", target, herdr=_contact(target).get("herdr_bin") or HERDR)
    except HerdrError:
        return "offline"
    agent = result.get("result", {}).get("agent", {})
    return agent.get("agent_status") or agent.get("status") or "unknown"


async def prompt(target: str, text: str) -> None:
    """Hand the text to the agent: its fm inbox when the contact names one, else type it and press Enter."""
    contact = _contact(target)
    if contact.get("inbox"):
        await _exec(FM_SEND, contact["inbox"], "--fire-and-forget", uuid.uuid4().hex[:16], text,
                    timeout=30)
        return
    await _run("agent", "prompt", target, text, herdr=contact.get("herdr_bin") or HERDR)
