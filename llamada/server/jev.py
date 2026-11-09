"""Jev (typesafe.ai's «System One», model jev-latest): quick choices about what the user just said.

Two questions, both a single choice with a confidence:
  * muletilla: what kind of phrase it was, so the recorded acknowledgement fits it
  * interrupcion: said over the agent, does he want it to stop, or is he only assenting ("ajá")

Off unless ~/.config/llamada/jev-on exists (checked on every use, no restart needed).
The key is read from ~/.config/llamada/jev-token and goes only in the Authorization header.
Anything slower than TIMEOUT, below FLOOR, or failing is «no decision» (None): the call goes on as before.
"""
import asyncio
import logging
from pathlib import Path
import time

import aiohttp

BASE = "https://api.typesafe.ai"
MODEL = "jev-latest"
TIMEOUT = 0.4         # seconds: past this the caller is better off without an answer
FLOOR = 0.6           # confidence below this is a doubt
WARM_EVERY = 10.0     # seconds between connection warm-ups

FILLER_CATEGORIES = {
    "pregunta": "Pregunta algo o pide información, un dato o un estado: «¿qué tareas tenemos?», "
                "«¿puedes decirme qué decisiones hay?», «dame el estatus del proyecto».",
    "orden": "Pide que se haga algo, da una instrucción, aprueba o autoriza: «revisa los registros», "
             "«regístralo», «sí, apruébalo», «te autorizo», «desactívalo», «documéntalo».",
    "saludo": "Saluda o comprueba que se le oye: «hola», «¿me escuchas?», «hola, ¿qué tal?».",
    "despedida": "Se despide o da por terminada la llamada: «adiós», «eso es todo», «ya puedes cortar».",
    "agradecimiento": "Da las gracias: «gracias», «muchas gracias», «gracias, perfecto».",
    "otro": "Cuenta algo, comenta, informa o nada de lo anterior: «ya nos dieron el visto bueno», "
            "«eso es normal», «mira».",
}
FILLER_INSTRUCTIONS = ("Una persona habla por teléfono con su agente de IA y acaba de decir `usuario_dijo` "
                       "(transcrito, puede tener errores). ¿Qué tipo de frase es? Elige la opción que mejor la "
                       "describa; si pide algo con forma de pregunta («¿puedes revisar X?»), es orden cuando "
                       "quiere una acción y pregunta cuando quiere una respuesta.")

INTERRUPTION_OPTIONS = {
    "interrupcion": "Quiere que el agente se calle o cambie de rumbo: lo detiene, lo corrige, dice que no, "
                    "pregunta algo, pide otra cosa o responde con información nueva: «espera», «no, no», "
                    "«para», «¿y eso?», «mejor haz otra cosa», «sí, te autorizo».",
    "asentimiento": "Solo asiente o muestra que sigue escuchando, sin pedir nada ni añadir información: "
                    "«ajá», «sí», «ok», «ya», «mmm», «claro», «perfecto», «ok, dale».",
}
INTERRUPTION_INSTRUCTIONS = ("El agente de IA le está hablando a una persona por teléfono y ella dijo `usuario_dijo` "
                             "(transcrito, puede tener errores) mientras el agente hablaba. ¿Lo interrumpe de "
                             "verdad o solo asiente para que el agente siga?")

QUESTIONS = {
    "muletilla": (FILLER_INSTRUCTIONS, FILLER_CATEGORIES),
    "interrupcion": (INTERRUPTION_INSTRUCTIONS, INTERRUPTION_OPTIONS),
}

log = logging.getLogger("llamada.jev")


def enabled(config_dir: Path) -> bool:
    return (config_dir / "jev-on").exists()


class Jev:
    """One HTTP session kept open across turns, so most questions skip the TLS handshake."""

    def __init__(self, config_dir: Path, base: str = BASE, timeout: float = TIMEOUT, floor: float = FLOOR) -> None:
        self.config_dir = config_dir
        self.base = base
        self.timeout = timeout
        self.floor = floor
        self.session: aiohttp.ClientSession | None = None
        self.warmed_at = 0.0

    def _key(self) -> str | None:
        try:
            return (self.config_dir / "jev-token").read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ttl_dns_cache=300, keepalive_timeout=60))
        return self.session

    async def ask(self, name: str, phrase: str, timeout: float | None = None) -> dict | None:
        """The raw answer {choice, confidence, probabilities, ms}, or None when there is none in time."""
        key = self._key()
        if not key:
            return None
        instructions, criteria = QUESTIONS[name]
        body = {"model": MODEL, "state": {"usuario_dijo": phrase},
                "questions": {name: {"type": "choice", "instructions": instructions, "criteria": criteria}}}
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout or self.timeout):
                async with self._session().post(f"{self.base}/v1/systemone", json=body,
                                                headers={"Authorization": f"Bearer {key}"}) as response:
                    if response.status != 200:
                        log.info("jev %s: http %s", name, response.status)
                        return None
                    answer = (await response.json())["answers"][name]
            choice, confidence = answer["choice"], float(answer["confidence"])
        except TimeoutError:
            log.info("jev %s: no answer in %.1fs", name, timeout or self.timeout)
            return None
        except (aiohttp.ClientError, KeyError, TypeError, ValueError) as exc:
            log.info("jev %s: failed (%s)", name, type(exc).__name__)
            return None
        if choice not in criteria:
            return None
        return {"choice": choice, "confidence": confidence, "probabilities": answer.get("probabilities", {}),
                "ms": round((time.monotonic() - started) * 1000)}

    async def choose(self, name: str, phrase: str) -> str | None:
        answer = await self.ask(name, phrase)
        if not answer:
            return None
        log.info("jev %s: %s %.2f in %d ms", name, answer["choice"], answer["confidence"], answer["ms"])
        return answer["choice"] if answer["confidence"] >= self.floor else None

    async def filler_category(self, phrase: str) -> str | None:
        return await self.choose("muletilla", phrase)

    async def interruption(self, phrase: str) -> str | None:
        return await self.choose("interrupcion", phrase)

    def warm(self) -> None:
        """Open the connection ahead of a question (he started talking); at most once every WARM_EVERY."""
        if time.monotonic() - self.warmed_at < WARM_EVERY or not self._key():
            return
        self.warmed_at = time.monotonic()
        asyncio.ensure_future(self._warm())

    async def _warm(self) -> None:
        try:
            async with asyncio.timeout(3):
                async with self._session().get(f"{self.base}/v1/models",
                                               headers={"Authorization": f"Bearer {self._key()}"}) as response:
                    await response.read()
        except (TimeoutError, aiohttp.ClientError):
            pass

    async def close(self) -> None:
        if self.session:
            await self.session.close()
