# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==12.0.0"]
# ///
"""Render every published asset from the brand in source/ and the font in fonts/.

    uv run scripts/render.py           write every asset
    uv run scripts/render.py --check   fail if a committed asset is out of date

The output paths are a contract: other repositories and the site link to them
from `main`, so a path is never renamed or removed. Change what is drawn freely.
"""

from __future__ import annotations

import sys
import tomllib
from functools import partial
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parent.parent
BRAND = tomllib.loads((ROOT / "source" / "brand.toml").read_text(encoding="utf-8"))
NAME = BRAND["name"]
TYPE = BRAND["type"]
FONT = ROOT / TYPE["font"]


def color(hex_value: str) -> tuple[int, int, int, int]:
    value = hex_value.removeprefix("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255)


GREEN = color(BRAND["palette"]["green"])
CHALK = color(BRAND["palette"]["chalk"])
BRASS = color(BRAND["palette"]["brass"])
CLEAR = (0, 0, 0, 0)

# Everything is drawn at SCALE times its final size and scaled down once,
# which anti-aliases the letters and the banner's corners.
SCALE = 4

# A text run: the text and the setting it is set in.
Run = tuple[str, dict[str, int]]


def font(setting: dict[str, int], size: float) -> ImageFont.FreeTypeFont:
    """The typeface at one of the brand's settings, with the basic layout engine.

    Pillow uses Raqm for text layout where its build has it (Linux) and the
    basic engine where it does not (macOS), and the two place glyphs a little
    differently. Forcing the basic engine renders identically everywhere.
    """
    face = ImageFont.truetype(str(FONT), round(size), layout_engine=ImageFont.Layout.BASIC)
    values = {"Weight": setting["weight"], "Width": setting["width"]}
    axes = []
    for axis in face.get_variation_axes():
        name = axis["name"]
        axes.append(values[name.decode() if isinstance(name, bytes) else name])
    face.set_variation_by_axes(axes)
    return face


def advance(text: str, face: ImageFont.FreeTypeFont) -> float:
    """How far text moves the pen, with the brand's join between J and W."""
    width = sum(face.getlength(char) for char in text)
    return width + text.count("JW") * TYPE["join"] * face.size


def typeset(runs: list[Run], size: float, fill: tuple[int, int, int, int]) -> tuple[Image.Image, float, float]:
    """Set runs of text on one baseline, each in its own setting.

    Letters are placed one at a time, so the J-W join is exact and no layout
    engine's kerning is involved. Returns the letters cropped to their ink,
    with the top of the capitals and the baseline inside that crop: layouts
    center on the capitals, not on the ink, which the g of "Engineering"
    pulls below the baseline.
    """
    faces = [font(setting, size) for _, setting in runs]
    spaces = [face.getlength(" ") * TYPE["space"] for face in faces[1:]]
    width = sum(advance(text, face) for (text, _), face in zip(runs, faces)) + sum(spaces)
    pad = size
    canvas = Image.new("RGBA", (round(width + 2 * pad), round(3 * size)), CLEAR)
    draw = ImageDraw.Draw(canvas)
    baseline = 2 * size
    x = pad
    for index, ((text, _), face) in enumerate(zip(runs, faces)):
        if index:
            x += spaces[index - 1]
        for position, char in enumerate(text):
            draw.text((x, baseline), char, font=face, fill=fill, anchor="ls")
            x += face.getlength(char)
            if text[position : position + 2] == "JW":
                x += TYPE["join"] * face.size
    box = canvas.getbbox()
    cap_height = -faces[0].getbbox("H", anchor="ls")[1]
    return canvas.crop(box), baseline - cap_height - box[1], baseline - box[1]


def typeset_to_width(runs: list[Run], width: float, fill: tuple[int, int, int, int]) -> tuple[Image.Image, float, float]:
    """typeset() at the size that makes the ink `width` pixels wide."""
    reference = 400
    measured = typeset(runs, reference, fill)[0].width
    return typeset(runs, reference * width / measured, fill)


def wordmark(width: float, fill: tuple[int, int, int, int]) -> tuple[Image.Image, float, float]:
    """The wordmark, "JWall" heavy and "Engineering" light, `width` pixels wide."""
    return typeset_to_width([(NAME["first"], TYPE["heavy"]), (NAME["second"], TYPE["light"])], width, fill)


def monogram(width: float, fill: tuple[int, int, int, int]) -> Image.Image:
    """The monogram, JW set as in the wordmark, `width` pixels wide."""
    return typeset_to_width([(NAME["monogram"], TYPE["heavy"])], width, fill)[0]


def paste_centered(canvas: Image.Image, image: Image.Image) -> tuple[int, int]:
    left = (canvas.width - image.width) // 2
    top = (canvas.height - image.height) // 2
    canvas.alpha_composite(image, (left, top))
    return left, top


def paste_on_capitals(canvas: Image.Image, ink: Image.Image, cap_top: float, baseline: float, left: float, middle: float) -> None:
    """Paste typeset ink with its left edge at `left` and its capitals centered on `middle`."""
    canvas.alpha_composite(ink, (round(left), round(middle - (cap_top + baseline) / 2)))


def require_inside_circle(canvas: Image.Image, image: Image.Image, at: tuple[int, int], limit: float = 0.8) -> None:
    """Fail the render if `image`, pasted at `at`, would reach past `limit` of the radius.

    GitHub crops avatars to the circle inscribed in the square; this keeps the
    monogram's corners well inside it.
    """
    radius = canvas.width / 2
    reach = max(
        ((x - radius) ** 2 + (y - radius) ** 2) ** 0.5
        for x in (at[0], at[0] + image.width)
        for y in (at[1], at[1] + image.height)
    )
    if reach > limit * radius:
        raise ValueError(f"the monogram reaches {reach / radius:.0%} of the avatar's radius, over {limit:.0%}")


def logo(size: int = 1024, width: float = 0.88) -> Image.Image:
    """The monogram in Green on a transparent square, for places that supply a background.

    Green reads on white and on GitHub's dark theme alike, so one file serves both.
    """
    s = SCALE
    canvas = Image.new("RGBA", (size * s, size * s), CLEAR)
    paste_centered(canvas, monogram(size * s * width, GREEN))
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def avatar(size: int = 1024, width: float = 0.66) -> Image.Image:
    """The monogram in Chalk on a solid Green square, for avatars cropped to a circle."""
    s = SCALE
    canvas = Image.new("RGBA", (size * s, size * s), GREEN)
    mark = monogram(size * s * width, CHALK)
    at = paste_centered(canvas, mark)
    require_inside_circle(canvas, mark, at)
    return canvas.resize((size, size), Image.Resampling.LANCZOS).convert("RGB")


def favicon(size: int) -> Image.Image:
    """The monogram on a Green square: the site's tab icons, and the Apple touch icon at 180.

    Tab icons are tiny and square, so the monogram fills most of the width.
    iOS rounds the touch icon's corners and some launchers mask it to a
    circle, so there it keeps the avatar's margins.
    """
    s = SCALE
    canvas = Image.new("RGBA", (size * s, size * s), GREEN)
    paste_centered(canvas, monogram(size * s * (0.66 if size >= 180 else 0.90), CHALK))
    return canvas.resize((size, size), Image.Resampling.LANCZOS).convert("RGB")


def banner(width: int = 1280, height: int = 320, margin: int = 112) -> Image.Image:
    """The README banner: the wordmark in Chalk across a Green band with rounded corners."""
    s = SCALE
    canvas = Image.new("RGBA", (width * s, height * s), CLEAR)
    ImageDraw.Draw(canvas).rounded_rectangle((0, 0, width * s - 1, height * s - 1), radius=24 * s, fill=GREEN)
    ink, cap_top, baseline = wordmark((width - 2 * margin) * s, CHALK)
    paste_on_capitals(canvas, ink, cap_top, baseline, (width * s - ink.width) / 2, height * s / 2)
    return canvas.resize((width, height), Image.Resampling.LANCZOS)


def social_preview(width: int = 1280, height: int = 640, margin: int = 128) -> Image.Image:
    """The repository social preview: the wordmark on Green, with the site in Brass below.

    Square-cornered and opaque: link cards crop the image to their own shape.
    """
    s = SCALE
    canvas = Image.new("RGBA", (width * s, height * s), GREEN)
    ink, cap_top, baseline = wordmark((width - 2 * margin) * s, CHALK)
    left = (width * s - ink.width) / 2
    paste_on_capitals(canvas, ink, cap_top, baseline, left, height * s * 0.47)
    site, _, site_baseline = typeset([(NAME["site"], TYPE["light"])], 40 * s, BRASS)
    canvas.alpha_composite(site, (round(left), round((height - margin * 0.75) * s - site_baseline)))
    return canvas.resize((width, height), Image.Resampling.LANCZOS).convert("RGB")


ASSETS = {
    "assets/logo/jwall-engineering-logo.png": logo,
    "assets/avatar/jwall-engineering-avatar.png": avatar,
    "assets/banner/jwall-engineering-readme-banner.png": banner,
    "assets/social/jwall-engineering-social-preview.png": social_preview,
    "assets/favicon/jwall-engineering-favicon-32.png": partial(favicon, 32),
    "assets/favicon/jwall-engineering-favicon-64.png": partial(favicon, 64),
    "assets/favicon/jwall-engineering-favicon-180.png": partial(favicon, 180),
}


# The largest mean difference per channel, out of 255, that still counts as
# the same image. It absorbs anti-aliasing differences between platforms and
# catches any real change to what is drawn.
TOLERANCE = 1.0


def differs(committed: Path, rendered: Image.Image) -> float | None:
    """The mean per-channel difference, or None when the sizes differ."""
    with Image.open(committed) as image:
        current = image.convert("RGBA")
    rendered = rendered.convert("RGBA")
    if current.size != rendered.size:
        return None
    return max(ImageStat.Stat(ImageChops.difference(current, rendered)).mean)


def main(argv: list[str]) -> int:
    check = argv == ["--check"]
    stale = []
    for relative, render in ASSETS.items():
        path = ROOT / relative
        image = render()
        if check:
            difference = differs(path, image) if path.is_file() else None
            if difference is None or difference > TOLERANCE:
                stale.append(relative)
                print(f"  STALE  {relative}  (difference: {difference})")
            else:
                print(f"  ok     {relative}  (difference: {difference:.3f})")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, optimize=True)
        print(f"{relative}  {image.width}x{image.height}  {path.stat().st_size // 1024} KiB")
    if stale:
        print("Run `uv run scripts/render.py` and commit the assets it writes.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
