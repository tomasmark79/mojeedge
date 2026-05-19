# MojeEdge — Edge TTS klient

Jednoduchý Python klient pro Microsoft Edge TTS.

## Instalace

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install edge-tts
```

## Použití

```bash
# Přehrání textu (vyžaduje mpg123 nebo ffplay)
python edge_tts_client.py "Ahoj, jak se máš?"

# Uložení do MP3 souboru
python edge_tts_client.py "Ahoj světe" -o vystup.mp3

# Jiný hlas
python edge_tts_client.py "Hello world" -v en-US-JennyNeural
```

## Parametry

| Parametr | Popis | Výchozí |
|----------|-------|---------|
| `text` | Text ke čtení | — |
| `-v`, `--voice` | Název hlasu | `cs-CZ-VlastaNeural` |
| `-o`, `--output` | Cesta k výstupnímu MP3 | přehrát přímo |

## Závislosti

- [edge-tts](https://github.com/rany2/edge-tts)
- `mpg123` nebo `ffplay` pro přímé přehrávání

---

# Discord TTS (`discord_tts.py`)

Sleduje zprávy v zadaných Discord kanálech přes Gateway WebSocket a čte je nahlas přes Edge TTS.

## Instalace

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install edge-tts aiohttp
```

Token bota se čte ze souboru `~/.ssh/ditwi.token`.

## Spuštění

```bash
python discord_tts.py
```

## Konfigurace (v hlavičce souboru)

| Proměnná | Popis |
|----------|-------|
| `CHANNEL_IDS` | Slovník `id → (název, jazyk)`, jazyk `"cs"` nebo `"en"` |
| `TTS_VOICE` | Název hlasu Edge TTS |
| `TTS_RATE` | Rychlost mluvení, např. `"+0%"`, `"+30%"` |
| `IGNORE_BOTS` | Ignorovat zprávy od botů |
| `ALLOWED_USERS` | Whitelist uživatelů (prázdné = vše) |
| `TRANSLATE` | Překládat `"en"` kanály do češtiny přes LibreTranslate |
| `LIBRETRANSLATE_URL` | Adresa LibreTranslate instance |
| `KOREKTOR_URL` | Adresa Korektoru pro doplnění diakritiky |

## Chování

- Kanály s jazykem `"cs"`: automaticky doplní diakritiku přes Korektor (LINDAT)
- Kanály s jazykem `"en"`: přeloží text do češtiny přes LibreTranslate
- TTS říká: `název_kanálu uživatel : zpráva`
- Automatický reconnect s Discord RESUME po výpadku spojení

## Závislosti

- [edge-tts](https://github.com/rany2/edge-tts)
- [aiohttp](https://github.com/aio-libs/aiohttp)
- `mpg123` nebo `ffplay` pro přehrávání
