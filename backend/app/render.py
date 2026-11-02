"""Draws shareable files on this computer: a phone-sized image card (1080x1350 PNG) and simple PDF
pages (A4). Hebrew is laid out right to left. Uses system fonts that include Hebrew (Windows: Segoe UI or
Arial; macOS: Arial; Linux: DejaVu Sans); nothing is downloaded."""
from __future__ import annotations

import io
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Same palette as the app (sRGB approximations of the OKLCH tokens, light theme)
BG = (247, 244, 239)
SURFACE = (255, 255, 255)
TEXT = (33, 29, 25)
MUTED = (110, 103, 96)
FAINT = (160, 153, 146)
LINE = (226, 221, 214)
ACCENT = (178, 84, 36)
GAIN = (46, 120, 78)
LOSS = (170, 60, 50)

FONT_CANDIDATES = {
    "regular": ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"],
    "bold": ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/Library/Fonts/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"],
}
HEB = re.compile(r"[\u0590-\u05FF]")


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES["bold" if bold else "regular"]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def is_rtl(text: str) -> bool:
    return bool(HEB.search(text or ""))


def visual(text: str) -> str:
    """Logical text -> display order (for right-to-left lines)."""
    if not is_rtl(text):
        return text
    from bidi.algorithm import get_display

    return get_display(text)


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Wraps logical text to lines that fit `width` pixels (measured in display order)."""
    out = []
    for para in (text or "").split("\n"):
        words, line = para.split(" "), ""
        for w in words:
            cand = (line + " " + w).strip()
            if f.getlength(visual(cand)) <= width or not line:
                line = cand
            else:
                out.append(line)
                line = w
        out.append(line)
    return out


class Canvas:
    def __init__(self, w: int, h: int, rtl: bool, margin: int):
        self.img = Image.new("RGB", (w, h), BG)
        self.d = ImageDraw.Draw(self.img)
        self.w, self.h, self.rtl, self.m = w, h, rtl, margin
        self.y = margin

    def text(self, s: str, size: int, color=TEXT, bold: bool = False, gap: int = 8, width: int | None = None, x0: int | None = None) -> int:
        f = font(size, bold)
        width = width or (self.w - 2 * self.m)
        lines = wrap(s, f, width)
        for ln in lines:
            disp = visual(ln)
            tw = f.getlength(disp)
            rtl = is_rtl(ln) or self.rtl
            x = (x0 if x0 is not None else self.m)
            if rtl:
                x = (x0 + width if x0 is not None else self.w - self.m) - tw
            self.d.text((x, self.y), disp, font=f, fill=color)
            self.y += int(size * 1.35)
        self.y += gap
        return len(lines)

    def rule(self, gap: int = 16) -> None:
        self.d.line([(self.m, self.y), (self.w - self.m, self.y)], fill=LINE, width=2)
        self.y += gap

    def pair(self, left: str, right: str, size: int, lcolor=TEXT, rcolor=TEXT, bold=False) -> None:
        """A label and a value on one line; mirrored for right-to-left."""
        f = font(size, bold)
        a, b = (right, left) if self.rtl else (left, right)
        ca, cb = (rcolor, lcolor) if self.rtl else (lcolor, rcolor)
        self.d.text((self.m, self.y), visual(a), font=f, fill=ca)
        self.d.text((self.w - self.m - f.getlength(visual(b)), self.y), visual(b), font=f, fill=cb)
        self.y += int(size * 1.45)

    def logo(self, path: Path | None, letter: str, x: int, y: int, size: int) -> None:
        if path and path.exists() and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"):
            try:
                im = Image.open(path).convert("RGBA")
                im.thumbnail((size, size))
                tile = Image.new("RGB", (size, size), SURFACE)
                tile.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
                self.img.paste(tile, (x, y))
                return
            except OSError:
                pass
        self.d.rounded_rectangle([x, y, x + size, y + size], radius=6, fill=LINE)
        f = font(int(size * 0.5), True)
        self.d.text((x + size / 2 - f.getlength(letter) / 2, y + size * 0.2), letter, font=f, fill=TEXT)

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def sign_color(v: float | None):
    return TEXT if v is None or abs(v) < 1e-9 else (GAIN if v > 0 else LOSS)


def pdf(pages: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=150, save_all=True, append_images=pages[1:])
    return buf.getvalue()


A4 = (1240, 1754)  # 150 dpi


class Document:
    """Flowing text over A4 pages."""

    def __init__(self, rtl: bool):
        self.rtl = rtl
        self.pages: list[Canvas] = []
        self.new_page()

    def new_page(self) -> None:
        self.c = Canvas(*A4, rtl=self.rtl, margin=100)
        self.pages.append(self.c)

    def ensure(self, px: int) -> None:
        if self.c.y + px > A4[1] - 100:
            self.new_page()

    def text(self, s: str, size: int = 26, color=TEXT, bold=False, gap: int = 8) -> None:
        f = font(size, bold)
        lines = wrap(s, f, A4[0] - 200)
        self.ensure(int(len(lines) * size * 1.35) + gap)
        self.c.text(s, size, color, bold, gap)

    def bytes(self) -> bytes:
        return pdf([p.img for p in self.pages])


# ------------------------------------------------------------------ the memo

def memo_pdf(memo: dict) -> bytes:
    he = memo["lang"] == "he"
    doc = Document(rtl=he)
    doc.text(memo["title"], 44, bold=True, gap=6)
    doc.text(("מזכר אנליסט · " if he else "Analyst memo · ") + memo["date"], 24, MUTED, gap=24)
    for s in memo["sections"]:
        doc.ensure(120)
        doc.text(f"{s['n']}. {s['title']}", 32, ACCENT, bold=True, gap=4)
        for line in s["lines"]:
            doc.text(("• " if not he else "") + line + (" •" if he else ""), 25, TEXT, gap=6)
        doc.c.y += 14
    return doc.bytes()


def memo_card(memo: dict) -> bytes:
    he = memo["lang"] == "he"
    c = Canvas(1080, 1350, rtl=he, margin=72)
    c.d.rectangle([0, 0, 1080, 14], fill=ACCENT)
    c.y = 70
    c.text(memo["title"], 50, bold=True, gap=10)
    a = memo["answer"]
    c.text(("התרחיש הסביר למשפחה" if he else "Most likely for the family"), 30, MUTED, gap=2)
    c.text(f"{_ils(a['median_ils'])}  ({_pct(a['median'])})", 72, sign_color(a["median_ils"]), bold=True, gap=4)
    c.text((f"במקרה רע (1 מתוך 20): {_ils(a['p5_ils'])}" if he else f"Bad case (1 in 20): {_ils(a['p5_ils'])}"), 32, MUTED, gap=28)
    c.rule()
    for b in memo["branches"][:4]:
        c.pair(f"{b['name']}  {round(b['probability'] * 100)}%", _pct(b["median"]), 32, TEXT, sign_color(b["median"]))
    c.y += 10
    c.rule()
    for p in sorted(memo["people"], key=lambda x: x["median_ils"])[:6]:
        c.pair(p["name"], _ils(p["median_ils"]), 32, TEXT, sign_color(p["median_ils"]))
    c.y = 1350 - 90
    c.text(("זה לא ייעוץ השקעות · " if he else "Not investment advice · ") + memo["date"], 24, FAINT)
    return c.png()


def _ils(v: float) -> str:
    return f"{'+' if v > 0 else '−' if v < 0 else ''}₪{abs(v):,.0f}"


def _pct(v: float) -> str:
    return f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v) * 100:.1f}%"
