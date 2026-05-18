#!/usr/bin/env python3
"""Edge TTS client — přečte text předaný jako argument příkazové řádky."""

import argparse
import asyncio
import sys
import tempfile
import os

try:
    import edge_tts
except ImportError:
    print("Chybí balíček edge-tts. Nainstaluj ho: pip install edge-tts", file=sys.stderr)
    sys.exit(1)


async def speak(text: str, voice: str, output_file: str | None) -> None:
    communicate = edge_tts.Communicate(text, voice)

    if output_file:
        await communicate.save(output_file)
        print(f"Uloženo: {output_file}")
    else:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await communicate.save(tmp_path)
            os.system(f"mpg123 -q {tmp_path} 2>/dev/null || ffplay -nodisp -autoexit -loglevel quiet {tmp_path}")
        finally:
            os.unlink(tmp_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Edge TTS klient")
    parser.add_argument("text", help="Text ke čtení")
    parser.add_argument(
        "-v", "--voice",
        default="cs-CZ-AntoninNeural",
        help="Hlas (výchozí: cs-CZ-AntoninNeural)",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Uložit audio do souboru místo přehrání",
    )
    args = parser.parse_args()

    asyncio.run(speak(args.text, args.voice, args.output))


if __name__ == "__main__":
    main()
