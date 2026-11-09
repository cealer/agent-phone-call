"""Jev (typesafe.ai's «System One», model jev-latest): quick choices about what the user just said.

Two questions, both a single choice with a confidence:
  * filler: what kind of phrase it was, so the recorded acknowledgement fits it
  * interruption: said over the agent, does the user want it to stop, or are they only assenting ("uh-huh")

Off unless ~/.config/phonecall/jev-on exists (checked on every use, no restart needed).
The key is read from ~/.config/phonecall/jev-token and goes only in the Authorization header.
Anything slower than TIMEOUT, below FLOOR, or failing is «no decision» (None): the call goes on as before.
The criteria carry examples in English and Spanish (the languages of language.py); add yours alongside.
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
    "question": "Asks something or asks for information, a fact or a status: «what tasks do we have?», "
                "«can you tell me which decisions are pending?», «give me the project status», "
                "«¿qué tareas tenemos?».",
    "order": "Asks for something to be done, gives an instruction, approves or authorizes: «check the logs», "
             "«write it down», «yes, approve it», «you have my go-ahead», «turn it off», «revísalo».",
    "greeting": "Says hello or checks they can be heard: «hello», «can you hear me?», «hi, how are you?», "
                "«hola, ¿me escuchas?».",
    "goodbye": "Says goodbye or ends the call: «bye», «that's all», «you can hang up now», «adiós».",
    "thanks": "Says thanks: «thanks», «thank you very much», «thanks, perfect», «gracias».",
    "other": "Tells something, comments, informs, or none of the above: «they already gave us the go-ahead», "
             "«that's normal», «look», «eso es normal».",
}
FILLER_INSTRUCTIONS = ("A person is talking on the phone with their AI agent and just said `user_said` "
                       "(transcribed, it may have errors). What kind of phrase is it? Choose the option that best "
                       "describes it; if they ask for something in the form of a question («can you check X?»), it "
                       "is an order when they want an action and a question when they want an answer.")

INTERRUPTION_OPTIONS = {
    "interruption": "Wants the agent to stop or change course: stops it, corrects it, says no, asks something, asks "
                    "for something else or answers with new information: «wait», «no, no», «stop», «what about "
                    "that?», «do something else instead», «yes, go ahead», «espera».",
    "assent": "Only assents or shows they are still listening, without asking for anything or adding information: "
              "«uh-huh», «yes», «ok», «right», «mm», «sure», «perfect», «ok, go on», «ajá».",
}
INTERRUPTION_INSTRUCTIONS = ("The AI agent is talking to a person on the phone and they said `user_said` "
                             "(transcribed, it may have errors) while the agent was talking. Do they really "
                             "interrupt it, or only assent so the agent goes on?")

QUESTIONS = {
    "filler": (FILLER_INSTRUCTIONS, FILLER_CATEGORIES),
    "interruption": (INTERRUPTION_INSTRUCTIONS, INTERRUPTION_OPTIONS),
}

log = logging.getLogger("phonecall.jev")


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
        body = {"model": MODEL, "state": {"user_said": phrase},
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
        return await self.choose("filler", phrase)

    async def interruption(self, phrase: str) -> str | None:
        return await self.choose("interruption", phrase)

    def warm(self) -> None:
        """Open the connection ahead of a question (the user started talking); at most once every WARM_EVERY."""
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
