#!/usr/bin/env python3
"""
render.py — прави Pinterest пин 1000x1500 от снимка + заглавие.
Чете от environment променливи: TITLE, IMAGE, KICKER, BRAND, ACCENT, OUT.
"""

import base64, html, mimetypes, os, pathlib, re, sys
import requests

HERE = pathlib.Path(__file__).parent
TEMPLATE = HERE / "template.html"
UA = "Mozilla/5.0 (compatible; novavarna-pin/1.0)"

ACCENT_DEFAULT = "#E8552B"
INK_DEFAULT = "#141821"
FONT = '"DejaVu Sans","Liberation Sans",Arial,sans-serif'

MAX_TITLE = 70


EMOJI = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF" "\U0000FE00-\U0000FE0F" "]+", flags=re.UNICODE)


def clean(s: str) -> str:
    s = html.unescape(html.unescape(s or ""))
    s = EMOJI.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def shorten(title: str, limit: int = MAX_TITLE) -> str:
    t = title.strip()
    if len(t) <= limit:
        return t
    m = re.match(r"^(.{25," + str(limit) + r"}?)\s*[:–—]\s+\S", t)
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.{30," + str(limit) + r"}?),\s+\S", t)
    if m:
        return m.group(1).strip()
    return t[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:–-") + "…"


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def image_data_uri(src: str) -> str:
    if src.startswith("data:"):
        return src
    if re.match(r"^https?:", src):
        r = requests.get(src, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
        if not mime.startswith("image/"):
            sys.exit(f"Not an image, got {mime}: {src}")
        data = r.content
    else:
        p = pathlib.Path(src)
        if not p.exists():
            sys.exit(f"No such file: {p}")
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        data = p.read_bytes()
    return f"data:{mime};base64," + base64.b64encode(data).decode()


FIT_JS = """
() => {
  const t = document.getElementById('title');
  t.style.webkitLineClamp = 'unset';
  t.style.display = 'block';
  const avail = 600 - 44 - 110 - (8 + 34);
  let size = 78;
  t.style.fontSize = size + 'px';
  while (t.scrollHeight > avail && size > 38) {
    size -= 2;
    t.style.fontSize = size + 'px';
  }
  if (t.scrollHeight > avail) {
    const lh = size * 1.12;
    t.style.display = '-webkit-box';
    t.style.webkitBoxOrient = 'vertical';
    t.style.webkitLineClamp = String(Math.max(1, Math.floor(avail / lh)));
    t.style.overflow = 'hidden';
  }
  return size;
}
"""


def main():
    title_raw = clean(os.environ.get("TITLE", ""))
    image = os.environ.get("IMAGE", "").strip()
    if not title_raw:
        sys.exit("Missing TITLE")
    if not image:
        sys.exit("Missing IMAGE")

    values = {
        "IMAGE": image_data_uri(image),
        "TITLE": esc(shorten(title_raw)),
        "KICKER": esc(clean(os.environ.get("KICKER", ""))),
        "BRAND": esc(clean(os.environ.get("BRAND", "")) or "novavarna.net"),
        "ACCENT": os.environ.get("ACCENT", "").strip() or ACCENT_DEFAULT,
        "INK": os.environ.get("INK", "").strip() or INK_DEFAULT,
        "TITLEFONT": FONT,
        "LANG": "bg",
    }

    page_html = TEMPLATE.read_text(encoding="utf-8")
    for k, v in values.items():
        page_html = page_html.replace("{{" + k + "}}", v)
    if not values["KICKER"]:
        page_html = page_html.replace('<div class="kicker"></div>', "")

    out = pathlib.Path(os.environ.get("OUT", "pin.png"))

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 1500},
                                device_scale_factor=1)
        page.set_content(page_html, wait_until="load")
        page.wait_for_timeout(200)
        size = page.evaluate(FIT_JS)
        page.screenshot(path=str(out))
        browser.close()

    print(f"pin ready: {out} | {values['TITLE']} | {size}px")


if __name__ == "__main__":
    main()
