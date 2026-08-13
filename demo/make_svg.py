"""
make_svg.py
───────────
Turn the asciicast written by gate_demo.py into a self-contained animated SVG.

asciinema has no Windows support and `agg`/`svg-term` are node/rust tools this
repo does not otherwise need, so this does the one job required: replay an
append-only terminal transcript as SVG that plays in a README on GitHub.

It is a deliberately small terminal emulator - it understands newlines,
carriage returns and SGR colour codes, which is all gate_demo.py emits. Anything
else (cursor movement, screen clears) is ignored rather than half-supported.

Usage:
    python demo/gate_demo.py --cast demo/gate_demo.cast
    python demo/make_svg.py demo/gate_demo.cast docs/gate-demo.svg
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROWS = 24          # visible rows; the transcript scrolls under this window
FONT_SIZE = 15
CHAR_W = 9.0       # advance width of a 15px monospace glyph
LINE_H = 21
PAD_X, PAD_Y = 18, 14
TITLE_H = 30
TAIL = 3.5         # seconds held on the last frame before the loop restarts

FONT = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'DejaVu Sans Mono', 'Liberation Mono', monospace")

BG, CHROME, FG = "#0d1117", "#161b22", "#c9d1d9"
COLOURS = {
    30: "#484f58", 31: "#ff7b72", 32: "#3fb950", 33: "#d29922",
    34: "#58a6ff", 35: "#bc8cff", 36: "#39c5cf", 37: "#b1bac4",
    90: "#6e7681", 91: "#ff7b72", 92: "#3fb950", 93: "#d29922",
    94: "#79c0ff", 95: "#d2a8ff", 96: "#56d4dd", 97: "#f0f6fc",
}

SGR = re.compile(r"\x1b\[([0-9;]*)m")


class Style:
    __slots__ = ("fg", "bold", "dim")

    def __init__(self, fg: str = FG, bold: bool = False, dim: bool = False):
        self.fg, self.bold, self.dim = fg, bold, dim

    def key(self):
        return (self.fg, self.bold, self.dim)

    def copy(self) -> "Style":
        return Style(self.fg, self.bold, self.dim)

    def apply(self, params: str) -> "Style":
        s = self.copy()
        for raw in (params or "0").split(";"):
            code = int(raw or 0)
            if code == 0:
                s = Style()
            elif code == 1:
                s.bold = True
            elif code == 2:
                s.dim = True
            elif code == 22:
                s.bold = s.dim = False
            elif code == 39:
                s.fg = FG
            elif code in COLOURS:
                s.fg = COLOURS[code]
        return s


def parse_cast(path: Path):
    """Replay the cast into (row, col, time, style, text) segments.

    The transcript is flattened into one character stream with a timestamp per
    character before anything is parsed. gate_demo.py types two characters at a
    time, so a colour escape is routinely split across two cast events - and an
    SGR regex run per event would miss it and leak raw ESC bytes into the SVG.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    events = [json.loads(ln) for ln in lines[1:] if ln.strip()]

    stream, times = [], []
    for t, kind, payload in events:
        if kind != "o":
            continue
        stream.append(payload)
        times.extend([t] * len(payload))
    stream = "".join(stream)

    segments: list[tuple[int, int, float, Style, str]] = []
    scrolls: list[tuple[float, int]] = [(0.0, 0)]
    style, row, col, top = Style(), 0, 0, 0

    def runs(a: int, b: int):
        """Split stream[a:b] into maximal runs sharing one timestamp.

        A typed line is one uninterrupted chunk between two colour escapes but
        arrives over dozens of events; keeping those apart is the typing.
        """
        i = a
        while i < b:
            j = i
            while j < b and times[j] == times[i]:
                j += 1
            yield stream[i:j], times[i]
            i = j

    pos = 0
    for m in SGR.finditer(stream):
        for text, t in runs(pos, m.start()):
            row, col, top = _emit(segments, scrolls, text, t, style, row, col, top)
        style = style.apply(m.group(1))
        pos = m.end()
    for text, t in runs(pos, len(stream)):
        row, col, top = _emit(segments, scrolls, text, t, style, row, col, top)

    leaked = {c for _, _, _, _, text in segments for c in text if ord(c) < 32}
    if leaked:
        raise SystemExit(f"unhandled control characters in cast: {leaked!r}")

    duration = max((t for t, _, _ in events), default=0.0) + TAIL
    return segments, scrolls, row + 1, duration, header


def _emit(segments, scrolls, chunk, t, style, row, col, top):
    """Write one styled chunk, splitting on the control characters we support."""
    for part in re.split(r"(\r\n|\n|\r)", chunk):
        if part in ("\r\n", "\n"):
            row, col = row + 1, 0
            if row - top >= ROWS:                 # the window scrolls, instantly
                top = row - ROWS + 1
                scrolls.append((t, top))
        elif part == "\r":
            col = 0
        elif part:
            segments.append((row, col, t, style, part))
            col += len(part)
    return row, col, top


def merge(segments):
    """Join neighbouring segments that share a row, time and style.

    gate_demo.py emits typed text two characters at a time, so those stay
    separate - which is the typing animation. Everything else collapses.
    """
    out: list[list] = []
    for row, col, t, style, text in segments:
        if out:
            p = out[-1]
            if p[0] == row and p[2] == t and p[3].key() == style.key() and p[1] + len(p[4]) == col:
                p[4] += text
                continue
        out.append([row, col, t, style, text])
    return out


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(segments, scrolls, total_rows, duration, title: str) -> str:
    width = int(84 * CHAR_W + 2 * PAD_X)
    height = int(ROWS * LINE_H + 2 * PAD_Y + TITLE_H)
    dur = round(duration, 2)

    def cycle(t: float) -> str:
        """keyTimes for an opacity step at `t` inside a looping cycle."""
        k = max(0.0, min(1.0, t / duration))
        return f"0;{k:.5f};{k:.5f};1"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{FONT}" font-size="{FONT_SIZE}">',
        f'<title>{esc(title)}</title>',
        f'<rect width="{width}" height="{height}" rx="8" fill="{BG}"/>',
        f'<path d="M0 8a8 8 0 0 1 8-8h{width - 16}a8 8 0 0 1 8 8v{TITLE_H - 8}H0z" fill="{CHROME}"/>',
    ]
    for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        parts.append(f'<circle cx="{20 + i * 18}" cy="{TITLE_H / 2}" r="5.5" fill="{c}"/>')
    parts.append(
        f'<text x="{width / 2}" y="{TITLE_H / 2 + 4.5}" text-anchor="middle" '
        f'font-size="12" fill="#6e7681">{esc(title)}</text>'
    )

    # Clip the transcript to the window so scrolled-away rows are not painted.
    top = TITLE_H + PAD_Y
    parts.append(
        f'<clipPath id="win"><rect x="0" y="{top - 4}" width="{width}" '
        f'height="{ROWS * LINE_H + 8}"/></clipPath>'
    )
    parts.append('<g clip-path="url(#win)">')

    # The scroll itself: a discrete translate, because terminals jump. Several
    # rows can spill in the same clock tick, so keep only the last offset for
    # any one timestamp - keyTimes has to be strictly increasing.
    steps: list[tuple[float, int]] = []
    for t, row in scrolls:
        if steps and abs(steps[-1][0] - t) < 1e-6:
            steps[-1] = (t, row)
        else:
            steps.append((t, row))
    values = ";".join(f"0 {-row * LINE_H}" for _, row in steps)
    keytimes = ";".join(f"{min(1.0, t / duration):.5f}" for t, _ in steps)
    scrolls = steps
    parts.append('<g>')
    if len(scrolls) > 1:
        parts.append(
            f'<animateTransform attributeName="transform" type="translate" '
            f'calcMode="discrete" values="{values}" keyTimes="{keytimes}" '
            f'dur="{dur}s" repeatCount="indefinite"/>'
        )

    for row, col, t, style, text in segments:
        x = PAD_X + col * CHAR_W
        y = top + row * LINE_H + FONT_SIZE
        attrs = f'fill="{style.fg}"'
        if style.bold:
            attrs += ' font-weight="700"'
        parts.append(
            f'<text x="{x:.1f}" y="{y}" {attrs} xml:space="preserve" opacity="0">'
            f'<animate attributeName="opacity" values="0;0;{0.65 if style.dim else 1};'
            f'{0.65 if style.dim else 1}" keyTimes="{cycle(t)}" dur="{dur}s" '
            f'repeatCount="indefinite"/>{esc(text)}</text>'
        )

    parts.append('</g></g></svg>')
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cast", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()

    segments, scrolls, rows, duration, header = parse_cast(args.cast)
    svg = render(merge(segments), scrolls, rows, duration,
                 header.get("title", "finLM"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    print(f"{args.out}: {rows} rows, {duration:.1f}s, {len(svg) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
