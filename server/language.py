"""What the call says and listens for, in the language the user speaks.

PHONECALL_LANGUAGE picks the pack: `en` (default) or `es`. The same setting tells Whisper which language to
transcribe (engine.py, engine_openai.py), which words mean the user is not done talking (server.py), the default
greeting, and the short recorded phrases make_fillers.py records. The agent prompts themselves are in English for
every language: the agent is told which language to speak back.

To add a language, copy one pack, translate the phrases (keep each clip under the length noted next to it) and
re-run make_fillers.py. The keys of `filler_categories` and `wait` are fixed: they are Jev's categories (jev.py).
"""
import os

PACKS = {
    "en": {
        "name": "English",
        "whisper": "en",
        "whisper_prompt": ("A phone conversation between a person and their AI assistant: firstmate, secondmate, "
                           "Tailscale, Herdr, Claude."),
        # Words an English sentence does not end on: the user has more to say.
        "trailing": {"and", "or", "but", "so", "because", "that", "which", "who", "the", "a", "an", "of", "to",
                     "in", "on", "at", "for", "with", "from", "by", "about", "if", "when", "while", "um", "uh",
                     "like", "my", "your", "our", "their", "is", "are", "was", "i", "we", "it's", "then", "also"},
        "greeting": "Yes? I'm listening.",
        "agent_unreachable": "I couldn't pass your message to the agent. Check Herdr on the Mac.",
        "agent_offline": "The agent disconnected. Hanging up.",
        "hermes_preamble": ("This conversation is by voice: answer in English, short and natural, meant to be "
                            "read aloud, with no markdown and no lists.\n\n"),
        # The recorded acknowledgements (make_fillers.py). fillers/: generic ones, <= 1.5 s each.
        "fillers": {"mm-okay": "Mm, okay.", "ok": "Okay.", "listening": "Yes, I'm listening.", "go-on": "Go on.",
                    "lets-see": "Mm, let's see."},
        # fillers/<category>/: the one that fits the kind of phrase, with Jev on.
        "filler_categories": {
            "question": {"lets-see": "Mm, let's see.", "let-me-check": "Let me check.", "checking": "I'll look."},
            "order": {"on-it": "Okay, on it.", "got-it": "Got it.", "doing-it": "Right, doing it."},
            "greeting": {"hi-listening": "Hi, I'm listening.", "hi-here": "Hi! I'm here."},
            "goodbye": {"bye": "Okay, bye.", "see-you": "Sure, see you."},
            "thanks": {"welcome": "You're welcome.", "pleasure": "My pleasure."},
            "other": {"mm-okay": "Mm, okay.", "i-see": "Okay, I see.", "uh-huh": "Uh-huh."},
        },
        # fillers/nod/: soft nods while the user talks on and on, <= 0.6 s each.
        "nods": {"uh-huh": "Uh-huh.", "mm": "Mm.", "right": "Right.", "yes": "Yes."},
        # fillers/mm/: with Jev on, the neutral clip played first, <= 0.5 s each.
        "mm": {"mm": "Mm.", "uh-huh": "Uh-huh."},
        # waiting/: the old layout of the waiting phrases, used only without fillers/wait/ ("first-*" go first).
        "waiting": {"first-checking": "Checking, one moment please.", "first-one-moment": "One moment, I'll check.",
                    "later-almost": "I almost have the answer.", "later-still": "Still checking, one more moment.",
                    "later-a-bit": "A little more and I'll tell you.", "later-nearly": "Nearly there."},
        # fillers/wait/<kind>/: «still working» phrases, <= 1.5 s each. The first one fits the kind of message
        # (question / order / other), then up to 3 of "more", then only the soft chime.
        "wait": {
            "question": {"let-me-check": "Let me check that.", "looking": "I'm looking at it."},
            "order": {"doing-it": "I'm on it.", "one-moment": "Give me a moment."},
            "other": {"one-moment": "One moment."},
            "more": {"still-on-it": "Still on it.", "almost": "Almost.", "a-bit-more": "A bit more.",
                     "nearly-done": "Nearly done."},
        },
    },
    "es": {
        "name": "Spanish",
        "whisper": "es",
        "whisper_prompt": ("Conversación telefónica en español entre una persona y su asistente de IA: firstmate, "
                           "secondmate, llamada, Tailscale, Herdr, Claude."),
        # Words a Spanish sentence does not end on: the user has more to say.
        "trailing": {"y", "o", "e", "u", "ni", "pero", "sino", "que", "de", "del", "el", "la", "los", "las", "un",
                     "una", "en", "con", "para", "por", "porque", "si", "como", "entonces", "eh", "mmm", "este", "a",
                     "al", "aunque", "cuando", "donde", "mi", "tu", "su", "lo", "me", "te", "se", "sea", "es", "son",
                     "está", "estoy"},
        "greeting": "¿Sí? Te escucho.",
        "agent_unreachable": "No pude pasarle tu mensaje al agente. Revisa herdr en la Mac.",
        "agent_offline": "El agente se desconectó. Cuelgo.",
        "hermes_preamble": ("Esta conversación es por voz: responde en español, breve y natural, pensado para "
                            "leerse en voz alta, sin markdown ni listas.\n\n"),
        "fillers": {"mmm-ya": "Mmm, ya.", "ok": "Ok.", "si-te-escucho": "Sí, te escucho.", "dime": "Dime.",
                    "mmm-a-ver": "Mmm, a ver."},
        "filler_categories": {
            "question": {"a-ver": "Mmm, a ver.", "dejame-ver": "Déjame ver.", "lo-reviso": "Lo reviso."},
            "order": {"ok-dale": "Ok, dale.", "entendido": "Entendido.", "listo-voy": "Listo, voy."},
            "greeting": {"hola-te-escucho": "Hola, te escucho.", "hola-aqui-estoy": "¡Hola! Aquí estoy."},
            "goodbye": {"listo-chao": "Listo, chao.", "hasta-luego": "Dale, hasta luego."},
            "thanks": {"de-nada": "De nada.", "con-gusto": "Con gusto."},
            "other": {"mmm-ya": "Mmm, ya.", "entiendo": "Ok, entiendo.", "aja": "Ajá."},
        },
        "nods": {"aja": "Ajá.", "mm": "Mm.", "ya": "Ya.", "si": "Sí."},
        "mm": {"mm": "Mm.", "aja": "Ajá."},
        "waiting": {"first-revisando": "Revisando un momento, por favor.",
                    "first-un-momento": "Un momento, lo reviso.",
                    "later-ya-casi": "Ya casi tengo la respuesta.",
                    "later-sigo": "Sigo revisando, un momento más.",
                    "later-un-poco": "Un poquito más y te cuento.",
                    "later-casi-listo": "Ya casi lo tengo."},
        "wait": {
            "question": {"dejame-revisarlo": "Déjame revisarlo.", "lo-estoy-mirando": "Lo estoy mirando."},
            "order": {"lo-estoy-haciendo": "Lo estoy haciendo.", "dame-un-momento": "Dame un momento."},
            "other": {"un-momento": "Un momento."},
            "more": {"sigo-con-ello": "Sigo con ello.", "ya-casi": "Ya casi.", "un-poco-mas": "Un poco más.",
                     "casi-listo": "Casi listo."},
        },
    },
}

LANGUAGE = (os.environ.get("PHONECALL_LANGUAGE") or "en").strip().lower()
PACK = PACKS.get(LANGUAGE) or PACKS["en"]
