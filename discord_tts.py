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
    309336971535187988: ("fufilm","cs"),
    642367764207763466:  ("fulinux","cs"),
    493746366456266763:  ("fudev","cs"),
    121395249003233280:  ("daw","en"),
    1359910160277045341: ("strejda","cs")
}
TTS_VOICE = "cs-CZ-AntoninNeural"
#TTS_VOICE = "cs-CZ-VlastaNeural"
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


async def heartbeat_loop(ws, interval_ms: float, get_seq) -> None:
    interval = interval_ms / 1000.0
    while True:
        await asyncio.sleep(interval)
        await ws.send_json({"op": 1, "d": get_seq()})


async def run_gateway(token: str, channel_ids: dict[int, tuple[str, str]], voice: str, rate: str) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    asyncio.get_event_loop().create_task(tts_worker(queue, voice, rate))

    session_id: str | None = None
    resume_gateway_url: str | None = None
    last_seq: int | None = None
    reconnect_delay = 1.0

    async with aiohttp.ClientSession() as session:
        while True:
            if resume_gateway_url and session_id:
                gateway_url = resume_gateway_url + "?v=10&encoding=json"
            else:
                gateway_url = GATEWAY_URL
            should_resume = bool(session_id and last_seq is not None)
            do_reconnect = False

            try:
                async with session.ws_connect(gateway_url) as ws:
                    hb_task: asyncio.Task | None = None

                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            do_reconnect = True
                            break

                        data = json.loads(msg.data)
                        op = data.get("op")
                        t = data.get("t")
                        d = data.get("d") or {}
                        s = data.get("s")
                        if s is not None:
                            last_seq = s

                        # HELLO — zahajeme heartbeat a odešleme IDENTIFY nebo RESUME
                        if op == 10:
                            interval = d["heartbeat_interval"]
                            hb_task = asyncio.get_event_loop().create_task(
                                heartbeat_loop(ws, interval, lambda: last_seq)
                            )
                            if should_resume:
                                await ws.send_json({
                                    "op": 6,
                                    "d": {
                                        "token": token,
                                        "session_id": session_id,
                                        "seq": last_seq,
                                    },
                                })
                            else:
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
                                session_id = d.get("session_id")
                                resume_gateway_url = d.get("resume_gateway_url")
                                reconnect_delay = 1.0
                                u = d.get("user", {})
                                print(f"[Discord TTS] Přihlášen jako {u.get('username')}#{u.get('discriminator')}. "
                                      f"Poslouchám kanály: {', '.join(f'{n} ({cid})' for cid, (n, *_) in channel_ids.items())}.", flush=True)

                            elif t == "RESUMED":
                                reconnect_delay = 1.0
                                print("[Discord TTS] Session úspěšně obnovena.", flush=True)

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

                                channel_name, lang = channel_ids[msg_channel_id]
                                clean = sanitize(text)
                                diac_restored = False
                                if lang == "cs":
                                    restored = await restore_diacritics(clean, session)
                                    diac_restored = restored != clean
                                    clean = restored
                                elif lang == "en" and TRANSLATE:
                                    clean = await translate(clean, session)
                                flags = " ✍" if diac_restored else ""
                                print(f"[{channel_name}][{author.get('username')}]{flags} {text}", flush=True)
                                tts_text = f"{sanitize(author.get('username', ''))} : {clean}"
                                await queue.put(tts_text)

                        # RECONNECT
                        elif op == 7:
                            print("[Discord TTS] Server žádá reconnect...", file=sys.stderr, flush=True)
                            do_reconnect = True
                            break

                        # INVALID SESSION
                        elif op == 9:
                            resumable = bool(d)
                            if not resumable:
                                session_id = None
                                last_seq = None
                                resume_gateway_url = None
                            print(f"[Discord TTS] Neplatná session (resumable={resumable}), restartuji...", file=sys.stderr, flush=True)
                            do_reconnect = True
                            break

                    if hb_task:
                        hb_task.cancel()

            except Exception as exc:
                print(f"[Discord TTS] Chyba WebSocket: {exc}", file=sys.stderr, flush=True)
                do_reconnect = True

            if not do_reconnect:
                break

            print(f"[Discord TTS] Reconnect za {reconnect_delay:.0f}s...", file=sys.stderr, flush=True)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60.0)


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
