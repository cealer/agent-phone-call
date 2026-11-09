---
name: phonecall
description: Phone calls with the user by voice through the `phonecall` command. Use when a prompt starts with "[phonecall]" (the user is on the phone with you right now: answer with `phonecall say`), when you are blocked mid-task on a decision, approval or fact only the user can give and they are away from the computer, or when they asked to be called ("call me when…", "if you get stuck, call me"). Not for things you can decide yourself, and not just to report progress unless they asked for that.
---

# Phone calls with the user

`phonecall` connects you to the user's Android phone (full-screen incoming call, even when locked)
in real time: you speak with the configured voice, they talk back, and they can interrupt you at
any moment. Speech recognition and the voice run on the user's Mac; audio travels over their
private network (Tailscale). The command works the same on that Mac and on another machine
(e.g. a VPS) that reaches it over the private network.

The `[phonecall]` messages are in English; the conversation itself is in the user's language
(the first message tells you which one to speak). `USER` in those messages is the user; `YOU` is you.

Two modes, chosen automatically:

- **Herdr mode**: you run inside Herdr (`HERDR_ENV=1`). The user's words reach you as prompts
  starting with `[phonecall]`; you answer with `phonecall say`, which returns at once.
  The user can also call *you* from the app (you are one of their contacts).
- **Turn mode**: you are not in Herdr. Each command blocks until the user answers and prints
  their words.

## When a prompt starts with "[phonecall]" (Herdr mode)

The user is on the line, waiting to hear you. Messages look like:

- `[phonecall] <Name> called you on the phone … USER said: …`: they called you; first words.
- `[phonecall] USER said: …`: what they just said.
- `… [USER interrupted you while YOU were speaking. YOU said (they heard): «…» · YOU did not get to say: «…»]`:
  they cut you off. Assume they only heard the first part; don't repeat it unless needed.
- `[phonecall] <Name> hung up the call.`: they pressed hang up; the call is over, don't use
  `phonecall` again.
- `[phonecall] The connection to the phone dropped.`: the line dropped; **they did not hang up**
  and the conversation was not finished. Keep its thread: they will likely call back, and then
  pick up where it was cut. Don't call them back yourself unless something still blocks you on them.

Answer **first, quickly**, with one shell call (no timeout needed, it returns at once):

```
phonecall say "spoken text"
```

Then do any work they asked for. If it takes more than a few seconds, first say what you
already know in a short `say` and speak the rest in another `say` when done. Consecutive
`say` calls play back to back with no gap (the next one is prepared while the previous still plays).
They hear a soft "thinking" sound while you work. Don't write a long chat reply: they are not
looking at the screen; the spoken text is the answer.

**Hanging up.** Only hang up when they say goodbye explicitly ("bye", "talk later", "hang up",
or the same in their language), and always with a goodbye: `phonecall hangup "text"` (never
without text; the server refuses it). "Thanks", "leave it like that", "close it" or "call it
done" are about the task, not the call: answer it and ask "Anything else?", then keep
listening. When in doubt, ask; don't hang up. If they stay silent, the call ends by itself
after a while.

## Calling the user

1. Be sure the call is worth it: one concrete thing you need from them that blocks you.
   Gather everything first (options, consequences, your recommendation) so the call is short.
2. `phonecall status` → `phone_registered` must be true and `call` null/ended. If the server
   is not running and you are on the Mac: `launchctl kickstart -k gui/$(id -u)/phonecall.server`.
   From another machine you cannot restart it: carry on and tell the user in writing.
3. `phonecall start "text"` with a **shell timeout of 10 minutes** (it rings up to 45 s).
   - Herdr mode: prints that they answered and heard you; their reply comes as a `[phonecall]` prompt.
   - Turn mode: prints their answer; continue with `phonecall say "…"` (also blocking, also
     10 minutes) and finish with `phonecall hangup "…"` (same rule: only after their
     goodbye, always with text). To say a first piece and keep
     working before the full answer, use `phonecall say --more "…"`: it returns at once
     without waiting, and your next `phonecall say` follows it with no gap (if they
     cut in meanwhile, that `say` reports it with exit `6`).

Exit codes: `0` ok (turn mode: their words on stdout) · `6` they interrupted you (stdout says
what they heard, what they didn't, and what they said) · `7` they spoke while you were thinking:
your text was **not** said; read their words and decide again · `3` no answer or declined ·
`4` they hung up · `5` they stayed silent (~60 s) · `1` error (message on stderr).

## How to speak (the text becomes speech)

- The user's language, warm and brief. When you call, open with who and why
  ("Hi, it's Claude. I'm calling about…").
- One clear question per turn. Give options and your recommendation when it is a decision.
- Plain spoken words only: no markdown, code, file paths, URLs or symbols. Say numbers and
  names the way a person would ("the payments branch", "about three hundred records").
- Short sentences; 1–3 sentences per `say` is ideal (each takes ~1–4 s to generate).
- Speech recognition can mishear. For anything important or destructive, repeat back what you
  understood and get a yes before acting.
- If they say something unclear, ask again rather than guess.

## If it does not go through

- `3` (no answer): don't call again right away. Continue with whatever does not need them,
  or wait at least 10 minutes before one more try. Never more than 3 attempts for the same thing.
- `4` mid-call (they hung up or the line dropped): treat what they already said as valid; don't
  call back unless it is still blocking.
- `5`: ask once more ("Are you still there?"); if silent again, hang up and continue as with `3`.

## After the call

Write in the chat a short summary: why the call happened, what they said (their decisions in
their words), and what you'll do now. That is the written record of the call.

Reference: the server and the app of the agent-phone-call repository (server log
`~/.config/phonecall/logs/server.log` on the Mac).
