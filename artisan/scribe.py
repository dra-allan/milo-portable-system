import json
import os
from pathlib import Path
from typing import Optional

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "scripts"


def generate_script(topic: str, style: str = "educational", duration_minutes: int = 10, niche: str = "investing") -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    script_templates = {
        "investing": {
            "educational": _investing_educational,
            "mistakes": _investing_mistakes,
            "comparison": _investing_comparison,
        }
    }

    generator = script_templates.get(niche, {}).get(style, _fallback)
    script = generator(topic, duration_minutes)

    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in topic)[:40]
    path = OUTPUT_DIR / f"{safe_name}.txt"
    path.write_text(script, encoding="utf-8")

    return str(path)


def _investing_educational(topic: str, duration: int) -> str:
    words = duration * 150
    return f"""# {topic}

## Hook (15 sec)
What if I told you that most people never learn {topic.lower()} — and it's costing them thousands of dollars every single year?

## Intro (30 sec)
Welcome back to Money Lab. I'm your host, and today we're breaking down {topic.lower()} in a way that actually makes sense. No jargon, no complicated formulas — just the facts you need to make better decisions with your money.

## Body ({duration-2} min)

### Point 1: The Basics
Let's start with what {topic.lower()} actually means. At its core, it's simpler than most people think.

### Point 2: Why It Matters
Here's why this is important for your financial future. The numbers don't lie — people who understand this concept earn significantly more over their lifetime.

### Point 3: How to Apply It
Now let's talk about how you can actually use this information starting today. Three actionable steps you can take right now.

### Point 4: Common Myths
There are a lot of misconceptions about this topic. Let me clear up the top three.

## Conclusion (30 sec)
So there you have it — {topic.lower()}, explained simply. If you found this helpful, hit subscribe and turn on notifications. Money Lab publishes new videos every Tuesday and Thursday to help you build real wealth.

**Disclaimer:** This is not financial advice. Always do your own research.
"""


def _investing_mistakes(topic: str, duration: int) -> str:
    return _investing_educational(topic, duration)


def _investing_comparison(topic: str, duration: int) -> str:
    return _investing_educational(topic, duration)


def _fallback(topic: str, duration: int) -> str:
    return _investing_educational(topic, duration)
