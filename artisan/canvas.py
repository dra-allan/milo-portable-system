from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "thumbnails"
FONT_DIR = Path(__file__).parent.parent.parent / "assets" / "fonts"


def _get_font(size: int = 60):
    fonts_to_try = [
        FONT_DIR / "BebasNeue-Regular.ttf",
        FONT_DIR / "Inter-Bold.ttf",
        Path("C:/Windows/Fonts/impact.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for fp in fonts_to_try:
        if fp.exists():
            return ImageFont.truetype(str(fp), size)
    return ImageFont.load_default()


def create_thumbnail(title: str, output_name: str, background_hex: str = "#0A1628") -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (1280, 720), background_hex)
    draw = ImageDraw.Draw(img)

    font_large = _get_font(72)
    font_small = _get_font(36)

    words = title.split()
    line1 = " ".join(words[:4])
    line2 = " ".join(words[4:])

    if line2:
        bbox1 = draw.textbbox((0, 0), line1, font=font_large)
        x1 = (1280 - (bbox1[2] - bbox1[0])) // 2
        y1 = 200
        draw.text((x1, y1), line1, fill="#FFFFFF", font=font_large)

        bbox2 = draw.textbbox((0, 0), line2, font=font_large)
        x2 = (1280 - (bbox2[2] - bbox2[0])) // 2
        y2 = 290
        draw.text((x2, y2), line2, fill="#00C9A7", font=font_large)
    else:
        bbox = draw.textbbox((0, 0), title, font=font_large)
        x = (1280 - (bbox[2] - bbox[0])) // 2
        y = 200
        draw.text((x, y), title, fill="#FFFFFF", font=font_large)

    bottom_text = "MONEY MATRIX"
    bbox3 = draw.textbbox((0, 0), bottom_text, font=font_small)
    x3 = (1280 - (bbox3[2] - bbox3[0])) // 2
    draw.text((x3, 580), bottom_text, fill="#FFD166", font=font_small)

    path = OUTPUT_DIR / f"{output_name}.jpg"
    img.save(str(path), "JPEG", quality=95)
    return str(path)
