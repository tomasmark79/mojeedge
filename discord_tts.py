#!/usr/bin/env python3
"""Discord TTS — sleduje zprávy v kanálu přes Gateway WebSocket a čte je přes Edge TTS."""

import asyncio
import json
import os
import re
import sys
import tempfile

# === KONFIGURACE ===
_TOKEN_FILE = os.path.expanduser("~/.ssh/ditwi.token")
try:
    with open(_TOKEN_FILE) as _f:
        DISCORD_TOKEN = _f.read().strip()
except OSError:
    print(f"Nelze načíst token ze souboru {_TOKEN_FILE}", file=sys.stderr)
    sys.exit(1)
# (název, jazyk)  — "cs": opravit diakritiku, "en": přeložit do češtiny
CHANNEL_IDS = {
    309336971535187988: ("fuxoft filmy",        "cs"),
    642367764207763466:  ("fuxoft linux",        "cs"),
    493746366456266763:  ("fuxoft programovani", "cs"),
    121395249003233280:  ("bitwig talk",         "en"),
    741347063077535874:  ("nixos general",       "en"),
    1359910160277045341: ("aistrejda obecna",    "cs"),
    846741885036396564:  ("geekboy pokec",    "cs")
}
#TTS_VOICE = "cs-CZ-AntoninNeural"
TTS_VOICE = "cs-CZ-VlastaNeural"
TTS_RATE = "+0%"   # rychlost mluvení: např. "-10%", "+0%", "+30%"
IGNORE_BOTS = True
ALLOWED_USERS: list[str] = []  # username nebo user id jako string; prázdné = vše
TRANSLATE = True
LIBRETRANSLATE_URL = "http://192.168.79.111:5000"
LIBRETRANSLATE_API_KEY = ""  # prázdné = bez klíče
KOREKTOR_URL = "https://lindat.mff.cuni.cz/services/korektor/api/correct"
# ===================

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

try:
    import aiohttp
except ImportError:
    print("Chybí balíček aiohttp. Nainstaluj ho: pip install aiohttp", file=sys.stderr)
    sys.exit(1)

try:
    import edge_tts
except ImportError:
    print("Chybí balíček edge-tts. Nainstaluj ho: pip install edge-tts", file=sys.stderr)
    sys.exit(1)


_CS_DIACRITICS = re.compile(r"[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")


def _needs_diacritics(text: str) -> bool:
    """Vrátí True pokud text nemá háčky/čárky, ale obsahuje alespoň 4 písmena."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and not _CS_DIACRITICS.search(text)


async def restore_diacritics(text: str, session: aiohttp.ClientSession) -> str:
    if not _needs_diacritics(text):
        return text
    try:
        async with session.post(
            KOREKTOR_URL,
            data={"model": "czech-diacritics_generator-130202", "data": text},
        ) as resp:
            data = await resp.json()
            return data.get("result", text)
    except Exception as exc:
        print(f"[Korektor] Chyba: {exc}", file=sys.stderr)
        return text


async def translate(text: str, session: aiohttp.ClientSession) -> str:
    payload: dict = {"q": text, "source": "auto", "target": "cs", "format": "text"}
    if LIBRETRANSLATE_API_KEY:
        payload["api_key"] = LIBRETRANSLATE_API_KEY
    try:
        async with session.post(f"{LIBRETRANSLATE_URL}/translate", json=payload) as resp:
            data = await resp.json()
            lang = data.get("detectedLanguage", {}).get("language", "")
            if lang == "cs":
                return text
            return data.get("translatedText", text)
    except Exception as exc:
        print(f"[Překlad] Chyba: {exc}", file=sys.stderr)
        return text


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF"   # symboly, piktogramy, doplňky
    "\U00002702-\U000027B0"    # Dingbats
    "\U00002600-\U000026FF"    # Různé symboly
    "\U00002B50-\U00002B55"    # hvězdy apod.
    "\U000023E9-\U000023FA"    # hodiny, tlačítka
    "\U0001F1E0-\U0001F1FF"    # vlajky (regional indicators)
    "︀-️"            # variation selectors
    "‍"                   # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


def sanitize(text: str) -> str:
    text = re.sub(r"https?://\S+", "odkaz", text)   # URL → "odkaz"
    text = re.sub(r"<[^>]+>", "", text)              # Discord mentions/emoji tagy
    text = _EMOJI_RE.sub("", text)                   # unicode emoji
    text = re.sub(r"[*_`~|\\]", "", text)            # markdown
    text = re.sub(r"[\[\](){}<>]", "", text)         # závorky všeho druhu
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def tts_play(text: str, voice: str, rate: str) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        await communicate.save(tmp_path)
        proc = await asyncio.create_subprocess_shell(
            f"mpg123 -q {tmp_path} 2>/dev/null || ffplay -nodisp -autoexit -loglevel quiet {tmp_path}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    finally:
        os.unlink(tmp_path)


async def tts_worker(queue: asyncio.Queue, voice: str, rate: str) -> None:
    while True:
        text = await queue.get()
        try:
            await tts_play(text, voice, rate)
        except Exception as exc:
            print(f"[TTS] Chyba přehrávání: {exc}", file=sys.stderr)
        finally:
            queue.task_done()


async def heartbeat_loop(ws, interval_ms: float) -> None:
    interval = interval_ms / 1000.0
    while True:
        await asyncio.sleep(interval)
        await ws.send_json({"op": 1, "d": None})


async def run_gateway(token: str, channel_ids: dict[int, tuple[str, str]], voice: str, rate: str) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    asyncio.get_event_loop().create_task(tts_worker(queue, voice, rate))

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(GATEWAY_URL) as ws:
            hb_task: asyncio.Task | None = None

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break

                data = json.loads(msg.data)
                op = data.get("op")
                t = data.get("t")
                d = data.get("d") or {}

                # HELLO — zahajeme heartbeat a odešleme IDENTIFY
                if op == 10:
                    interval = d["heartbeat_interval"]
                    hb_task = asyncio.get_event_loop().create_task(
                        heartbeat_loop(ws, interval)
                    )
                    await ws.send_json({
                        "op": 2,
                        "d": {
                            "token": token,
                            "capabilities": 16381,
                            "properties": {
                                "os": "Linux",
                                "browser": "Chrome",
                                "device": "",
                            },
                            "presence": {"status": "online", "afk": False},
                            "compress": False,
                            "client_state": {
                                "guild_versions": {},
                                "highest_last_message_id": "0",
                                "read_state_version": 0,
                                "user_guild_settings_version": -1,
                            },
                        },
                    })

                # DISPATCH
                elif op == 0:
                    if t == "READY":
                        u = d.get("user", {})
                        print(f"[Discord TTS] Přihlášen jako {u.get('username')}#{u.get('discriminator')}. "
                              f"Poslouchám kanály: {', '.join(f'{n} ({cid})' for cid, (n, *_) in channel_ids.items())}.", flush=True)

                    elif t == "MESSAGE_CREATE":
                        msg_channel_id = int(d.get("channel_id", 0))
                        if msg_channel_id not in channel_ids:
                            continue

                        author = d.get("author", {})
                        is_bot = author.get("bot", False)
                        if IGNORE_BOTS and is_bot:
                            continue

                        if ALLOWED_USERS:
                            uid = str(author.get("id", ""))
                            uname = author.get("username", "")
                            if uid not in ALLOWED_USERS and uname not in ALLOWED_USERS:
                                continue

                        text = d.get("content", "").strip()
                        if not text:
                            continue

                        _, lang = channel_ids[msg_channel_id]
                        clean = sanitize(text)
                        diac_restored = False
                        if lang == "cs":
                            restored = await restore_diacritics(clean, session)
                            diac_restored = restored != clean
                            clean = restored
                        elif lang == "en" and TRANSLATE:
                            clean = await translate(clean, session)
                        flags = " ✍" if diac_restored else ""
                        print(f"[{author.get('username')}]{flags} {text}", flush=True)
                        tts_text = f"{sanitize(author.get('username', ''))} píše: {clean}"
                        await queue.put(tts_text)

                # RECONNECT
                elif op == 7:
                    print("[Discord TTS] Server žádá reconnect...", file=sys.stderr)
                    break

                # INVALID SESSION
                elif op == 9:
                    print("[Discord TTS] Neplatná session, ukončuji.", file=sys.stderr)
                    break

            if hb_task:
                hb_task.cancel()


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN", DISCORD_TOKEN)
    channel_ids = {
        int(cid): cid.strip() for cid in os.environ.get("DISCORD_CHANNEL_IDS", "").split(",")
        if cid.strip()
    } or CHANNEL_IDS

    try:
        asyncio.run(run_gateway(token, channel_ids, TTS_VOICE, TTS_RATE))
    except KeyboardInterrupt:
        print("\n[Discord TTS] Ukončeno.")


if __name__ == "__main__":
    main()
