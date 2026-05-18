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
