import asyncio
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "audio"


async def generate_voiceover(script_path: str, voice: str = "en-US-GuyNeural") -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    script = Path(script_path).read_text(encoding="utf-8")
    lines = [l for l in script.split("\n") if l.strip() and not l.strip().startswith("#")]

    import edge_tts
    safe_name = Path(script_path).stem
    output_path = OUTPUT_DIR / f"{safe_name}.mp3"

    communicate = edge_tts.Communicate("\n\n".join(lines), voice)
    await communicate.save(str(output_path))

    return str(output_path)
