#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Нова Варна — дневен вертикален клип.

Сцени 1..N -> снимка на новина на цял кадър, която се движи отдясно наляво,
              плюс лого на синьо поле и заглавие на бяло полупрозрачно поле
Една от сцените носи рекламния банер долу.
Последна сцена -> финален кадър: въртящо се лого + банер novavarna.net + глас.

Гласът се прави с edge-tts (bg-BG-BorislavNeural, безплатно).

Вход: променливи на средата или --json файл.
Изход: out/clip.mp4
"""

import asyncio
import html
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import urlopen, Request

# ---------------------------------------------------------------- настройки

W, H = 1080, 1920
FPS = 30
BG_COLOR = "0x0B1424"
XFADE = 0.5                    # преход между сцените

LEAD_IN = 0.40                 # тишина преди гласа
TAIL = 0.90                    # тишина след гласа
OUTRO_LEAD_IN = 0.60
OUTRO_DURATION = 6.0

VOICE = os.environ.get("NV_VOICE", "bg-BG-BorislavNeural")
VOICE_RATE = os.environ.get("NV_VOICE_RATE", "-6%")
VOICE_PITCH = os.environ.get("NV_VOICE_PITCH", "+0Hz")
VOICE_VOLUME = os.environ.get("NV_VOICE_VOLUME", "+0%")
AUDIO_FILTER = os.environ.get(
    "NV_AUDIO_FILTER",
    "highpass=f=80,"
    "equalizer=f=2600:width_type=o:width=1.4:g=3,"
    "acompressor=threshold=-18dB:ratio=3:attack=8:release=180,"
    "loudnorm=I=-15:TP=-1.5:LRA=9,"
    "alimiter=limit=0.94",
)

LOGO_URL = os.environ.get("NV_LOGO_URL", "assets/logo.png")
BANNER_AD_URL = os.environ.get(
    "NV_BANNER_AD_URL",
    "https://novavarna.net/wp-content/uploads/2026/09/banner_reklama.png",
)
BANNER_SITE_URL = os.environ.get(
    "NV_BANNER_SITE_URL",
    "https://novavarna.net/wp-content/uploads/2026/09/banner_novavarna.png",
)

# движение на снимката
PAN_SPAN = 0.34                # с колко е по-широка снимката от кадъра
PHOTO_BRIGHTNESS = -0.07
PHOTO_SATURATION = 0.95

# лого и заглавие
CARD_X = 100                   # ляв ръб на двете полета
CARD_W = 880
CARD_BOTTOM = 1440             # долен ръб на заглавието
CARD_PAD_X = 34
CARD_PAD_Y = 26
CARD_RADIUS = 10
CARD_BG = (255, 255, 255, 224)  # бяло, леко прозрачно
CARD_FG = (15, 18, 24, 255)
CARD_FONT_SIZE = 46
CARD_LINE_GAP = 12

def _rgba(env_name, default):
    raw = os.environ.get(env_name, "")
    try:
        parts = [int(x) for x in raw.split(",")]
        if len(parts) == 4:
            return tuple(parts)
    except ValueError:
        pass
    return default


LOGO_PANEL_BG = _rgba("NV_LOGO_PANEL_BG", (43, 124, 192, 232))
LOGO_PANEL_PAD = 18
LOGO_IN_PANEL_W = 700
PANEL_GAP = 14                 # разстояние между логото и заглавието

# банери
BANNER_X, BANNER_Y, BANNER_W = 60, 1560, 960
BANNER_RISE = 0.55
BANNER_AD_START = 1.20
BANNER_SITE_START = 2.60
AD_SCENE = int(os.environ.get("NV_AD_SCENE", "2"))   # коя сцена носи рекламата

# финален кадър
OUTRO_LOGO_W = 840
OUTRO_LOGO_CY = 800
OUTRO_SPINS = 1.0
OUTRO_SPIN_TIME = 2.4
OUTRO_BG_ZOOM = 0.10           # приближаване на мозайката
OUTRO_BG_BLUR = 16             # размиване на мозайката
OUTRO_DIM_ALPHA = 0.62         # колко я потъмнява тъмносиньото

SUBTITLES = os.environ.get("NV_SUBTITLES", "0") == "1"
SUB_Y = 1520
SUB_SIZE = 56
SUB_MAX_WORDS = 4
SUB_FILL = "&H00FFFFFF"
SUB_ACTIVE = "&H00BD7941"
SUB_OUTLINE = "&H001F1208"

FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Bold.ttf",
    "/usr/share/fonts/truetype/roboto/hinted/Roboto-Bold.ttf",
    "/usr/share/fonts/truetype/google-fonts/Roboto-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

WORK = Path(os.environ.get("NV_WORK", "work"))
OUT = Path(os.environ.get("NV_OUT", "out"))


# ---------------------------------------------------------------- помощни


def log(msg):
    print(f"[nv] {msg}", flush=True)


def run(cmd, **kw):
    log("$ " + " ".join(str(c) for c in cmd[:6]) + (" ..." if len(cmd) > 6 else ""))
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        sys.stderr.write(p.stdout[-4000:] + "\n" + p.stderr[-8000:] + "\n")
        raise SystemExit(f"командата се провали: {cmd[0]}")
    return p


def fetch(url, dest: Path) -> Path:
    """Приема адрес в интернет или път до файл в хранилището."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if "://" not in url:
        src = Path(url)
        if not src.exists():
            raise SystemExit(f"липсва файлът {src}")
        shutil.copyfile(src, dest)
        log(f"взет {src} -> {dest} ({dest.stat().st_size} B)")
        return dest
    req = Request(url, headers={"User-Agent": "nova-varna-render/1.0"})
    with urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    if dest.stat().st_size == 0:
        raise SystemExit(f"празен файл от {url}")
    log(f"свален {url} -> {dest} ({dest.stat().st_size} B)")
    return dest


def pick_font() -> str:
    for p in FONT_BOLD_CANDIDATES:
        if Path(p).exists():
            return p
    hits = subprocess.run(
        ["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True, text=True
    )
    if hits.returncode == 0 and hits.stdout.strip():
        return hits.stdout.strip()
    raise SystemExit("не намерих подходящ шрифт")


def probe_duration(path: Path) -> float:
    p = run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ]
    )
    return float(p.stdout.strip())


def clean_title(raw: str) -> str:
    t = html.unescape(raw or "")
    t = re.sub(r"<[^>]+>", "", t)
    t = unicodedata.normalize("NFC", t)
    t = t.replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- полета


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_title_card(text: str, font_path: str, dest: Path) -> Path:
    """Бяло полупрозрачно поле със заглавието, като в примера."""
    from PIL import Image, ImageDraw, ImageFont

    size = CARD_FONT_SIZE
    inner = CARD_W - 2 * CARD_PAD_X
    probe = Image.new("RGBA", (10, 10))
    d0 = ImageDraw.Draw(probe)
    while size > 26:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(d0, text, font, inner)
        if len(lines) <= 4:
            break
        size -= 3
    font = ImageFont.truetype(font_path, size)
    lines = _wrap(d0, text, font, inner)

    asc, desc = font.getmetrics()
    lh = asc + desc
    height = 2 * CARD_PAD_Y + len(lines) * lh + (len(lines) - 1) * CARD_LINE_GAP

    im = Image.new("RGBA", (CARD_W, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, CARD_W - 1, height - 1], radius=CARD_RADIUS,
                        fill=CARD_BG)
    y = CARD_PAD_Y
    for ln in lines:
        d.text((CARD_PAD_X, y), ln, font=font, fill=CARD_FG)
        y += lh + CARD_LINE_GAP
    im.save(dest)
    log(f"заглавие: {len(lines)} реда, {size}px -> {dest}")
    return dest


def make_logo_panel(logo: Path, dest: Path) -> Path:
    """Логото върху синьо полупрозрачно поле, широко колкото заглавието."""
    from PIL import Image, ImageDraw

    src = Image.open(logo).convert("RGBA")
    lw = LOGO_IN_PANEL_W
    lh = max(1, round(src.height * lw / src.width))
    src = src.resize((lw, lh), Image.LANCZOS)

    height = lh + 2 * LOGO_PANEL_PAD
    im = Image.new("RGBA", (CARD_W, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, CARD_W - 1, height - 1], radius=CARD_RADIUS,
                        fill=LOGO_PANEL_BG)
    im.alpha_composite(src, (CARD_PAD_X, LOGO_PANEL_PAD))
    im.save(dest)
    log(f"лого-поле: {CARD_W}x{height} -> {dest}")
    return dest


# ---------------------------------------------------------------- глас


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Speech:
    path: Path
    duration: float
    words: list = field(default_factory=list)


async def _synth(text: str, dest: Path):
    import edge_tts

    words = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    comm = edge_tts.Communicate(
        text, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH, volume=VOICE_VOLUME
    )
    with open(dest, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append(
                    Word(chunk["text"], start, start + chunk["duration"] / 1e7)
                )
    return words


def estimate_words(text: str, total: float):
    ws = [w for w in text.split() if w]
    if not ws:
        return []
    weights = [len(w) + 1.3 for w in ws]
    s = sum(weights)
    lead = 0.04 * total
    span = max(total - lead - 0.08 * total, 0.4)
    out, t = [], lead
    for w, k in zip(ws, weights):
        d = span * k / s
        out.append(Word(w, t, t + d * 0.94))
        t += d
    return out


def synth(text: str, dest: Path) -> Speech:
    words = asyncio.run(_synth(text, dest))
    dur = probe_duration(dest)
    src = "от синтезатора"
    if not words:
        words = estimate_words(text, dur)
        src = "изчислени"
    log(f"глас: {dur:.2f}s, {len(words)} думи {src} — {text[:60]}")
    return Speech(dest, dur, words)


# ---------------------------------------------------------------- субтитри


def ass_time(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600):d}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def ass_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build_ass(blocks, font_path: str, dest: Path):
    low = font_path.lower()
    font_name = next(
        (fam for key, fam in (("roboto", "Roboto"), ("dejavu", "DejaVu Sans"),
                              ("liberation", "Liberation Sans"))
         if key in low),
        Path(font_path).stem,
    )
    lines = [f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: NV,{font_name},{SUB_SIZE},{SUB_FILL},{SUB_FILL},{SUB_OUTLINE},{SUB_OUTLINE},-1,0,0,0,100,100,0,0,1,5,3,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""]
    for offset, sp in blocks:
        words = [w for w in sp.words if w.text.strip()]
        for i in range(0, len(words), SUB_MAX_WORDS):
            group = words[i:i + SUB_MAX_WORDS]
            last_group = i + SUB_MAX_WORDS >= len(words)
            for j, w in enumerate(group):
                start = offset + w.start
                if j + 1 < len(group):
                    end = offset + group[j + 1].start
                elif not last_group:
                    end = offset + words[i + SUB_MAX_WORDS].start
                else:
                    end = offset + max(w.end, sp.duration)
                end = max(end, start + 0.12)
                parts = []
                for k, g in enumerate(group):
                    txt = ass_escape(g.text)
                    parts.append(
                        f"{{\\c{SUB_ACTIVE}}}{txt}{{\\c{SUB_FILL}}}"
                        if k == j else txt
                    )
                body = "{\\pos(%d,%d)}" % (W // 2, SUB_Y) + " ".join(parts)
                lines.append(
                    f"Dialogue: 0,{ass_time(start)},{ass_time(end)},NV,,0,0,0,,{body}"
                )
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"субтитри: {len(lines) - 1} реплики")
    return dest


# ---------------------------------------------------------------- сцени


def rise_expr(y_final: int, start: float, rise: float) -> str:
    travel = H - y_final
    p = f"min(max((t-{start:.2f})/{rise:.2f},0),1)"
    return f"'{H}-{travel}*(1-pow(1-{p},3))'"


def make_outro_mosaic(photos, dest: Path) -> Path:
    """Мозайка от снимките на новините — фон за финалния кадър."""
    from PIL import Image

    cols, rows = 2, 3
    cw, ch = 700, 830                      # по-голямо от кадъра, за да има накъде да мърда
    canvas = Image.new("RGB", (cols * cw, rows * ch), (11, 20, 36))
    pool = list(photos)
    while len(pool) < cols * rows:
        pool += list(photos)
    for idx in range(cols * rows):
        try:
            im = Image.open(pool[idx]).convert("RGB")
        except Exception:
            continue
        s = max(cw / im.width, ch / im.height)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))),
                       Image.LANCZOS)
        left = (im.width - cw) // 2
        top = (im.height - ch) // 2
        canvas.paste(im.crop((left, top, left + cw, top + ch)),
                     ((idx % cols) * cw, (idx // cols) * ch))
    canvas.save(dest, quality=90)
    log(f"мозайка за финала: {canvas.size} -> {dest}")
    return dest


def make_glow(dest: Path, size: int = 1200,
              color=(60, 150, 225), peak: int = 150) -> Path:
    """Мек кръгъл ореол в синьото на медията."""
    from PIL import Image

    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = im.load()
    r = size / 2
    for y in range(size):
        dy = (y - r) / r
        for x in range(size):
            dx = (x - r) / r
            d = (dx * dx + dy * dy) ** 0.5
            if d >= 1:
                continue
            a = int(peak * (1 - d) ** 2.2)
            if a:
                px[x, y] = (color[0], color[1], color[2], a)
    im.save(dest)
    log(f"ореол: {size}x{size} -> {dest}")
    return dest


def png_size(path: Path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def render_news_scene(photo: Path, panel: Path, card: Path, banner,
                      duration: float, dest: Path):
    card_h = png_size(card)[1]
    panel_h = png_size(panel)[1]
    card_y = CARD_BOTTOM - card_h
    panel_y = card_y - PANEL_GAP - panel_h
    wide = int(W * (1 + PAN_SPAN)) // 2 * 2
    span = wide - W
    # плавно движение отдясно наляво: прозорецът се мести надясно
    p = f"min(t/{duration:.3f},1)"
    pan = f"'{span}*({p}*{p}*(3-2*{p}))'"

    inputs = []
    for src in (photo, panel, card):
        inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(src)]
    if banner:
        inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(banner)]

    fc = [
        f"[0:v]scale={wide}:{H}:force_original_aspect_ratio=increase,"
        f"crop={wide}:{H},"
        f"eq=brightness={PHOTO_BRIGHTNESS}:saturation={PHOTO_SATURATION},"
        f"crop={W}:{H}:x={pan}:y=0,setsar=1,fps={FPS}[bg]",
        "[1:v]setsar=1[panel]",
        "[2:v]setsar=1[card]",
    ]
    # заглавието е долу, логото точно над него
    fc.append(f"[bg][card]overlay=x={CARD_X}:y={card_y}[v1]")
    fc.append(f"[v1][panel]overlay=x={CARD_X}:y={panel_y}[v2]")
    last = "v2"
    if banner:
        fc.append(f"[3:v]scale={BANNER_W}:-1,setsar=1[ban]")
        fc.append(
            f"[{last}][ban]overlay=x={BANNER_X}:"
            f"y={rise_expr(BANNER_Y, BANNER_AD_START, BANNER_RISE)}[v3]"
        )
        last = "v3"
    fc.append(f"[{last}]format=yuv420p[out]")

    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-t", f"{duration:.3f}", str(dest)])
    return dest


def render_outro_scene(logo: Path, banner: Path, mosaic: Path, glow: Path,
                       duration: float, dest: Path):
    box = int(OUTRO_LOGO_W * 1.15) // 2 * 2
    p = f"min(t/{OUTRO_SPIN_TIME:.2f},1)"
    angle = f"{2 * 3.14159265 * OUTRO_SPINS:.5f}*(1-pow(1-{p},3))"
    frames = max(int(duration * FPS), 2)
    gw = png_size(glow)[0]

    inputs = []
    for src in (logo, banner, mosaic, glow):
        inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(src)]

    fc = [
        # мозайката бавно се приближава, размита и потъмнена
        f"[2:v]scale={int(W * 1.25)}:{int(H * 1.25)}:force_original_aspect_ratio=increase,"
        f"crop={int(W * 1.25)}:{int(H * 1.25)},"
        f"zoompan=z='1+{OUTRO_BG_ZOOM:.3f}*on/{frames}':d=1:s={W}x{H}:fps={FPS}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
        f"gblur=sigma={OUTRO_BG_BLUR},eq=brightness=-0.30:saturation=0.55,"
        f"setsar=1[mos]",
        f"color=c={BG_COLOR}:s={W}x{H}:d={duration:.3f}:r={FPS},"
        f"format=rgba,colorchannelmixer=aa={OUTRO_DIM_ALPHA}[dim]",
        "[mos][dim]overlay=0:0[bg]",
        # мек ореол, който диша
        f"[3:v]format=rgba,fade=t=in:st=0.2:d=1.4:alpha=1[glowa]",
        f"[bg][glowa]overlay=x={(W - gw) // 2}:y={OUTRO_LOGO_CY - gw // 2}[v0]",
        f"[0:v]scale={OUTRO_LOGO_W}:-1,setsar=1,"
        f"pad={box}:{box}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"format=rgba,rotate=a='{angle}':c=black@0:ow={box}:oh={box}[logo]",
        f"[v0][logo]overlay=x={(W - box) // 2}:y={OUTRO_LOGO_CY - box // 2}[v1]",
        f"[1:v]scale={BANNER_W}:-1,setsar=1[ban]",
        f"[v1][ban]overlay=x={BANNER_X}:"
        f"y={rise_expr(BANNER_Y, BANNER_SITE_START, BANNER_RISE)}[v2]",
        f"[v2]fps={FPS},format=yuv420p[out]",
    ]
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-t", f"{duration:.3f}", str(dest)])
    return dest


def stitch_video(scenes, durations, dest: Path):
    starts = [0.0]
    for d in durations[:-1]:
        starts.append(starts[-1] + d - XFADE)
    inputs = []
    for s in scenes:
        inputs += ["-i", str(s)]
    fc, last = [], "0:v"
    elapsed = durations[0]
    for i in range(1, len(scenes)):
        offset = elapsed - XFADE
        tag = f"x{i}"
        fc.append(f"[{last}][{i}:v]xfade=transition=fade:duration={XFADE}:"
                  f"offset={offset:.3f}[{tag}]")
        last = tag
        elapsed = offset + durations[i]
    fc.append(f"[{last}]format=yuv420p[out]")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(dest)])
    return starts, elapsed


def build_audio(speeches, starts, leadins, total: float, dest: Path):
    inputs = []
    for sp in speeches:
        inputs += ["-i", str(sp.path)]
    fc, tags = [], []
    for i, (sp, st, lead) in enumerate(zip(speeches, starts, leadins)):
        delay = int(round((st + lead) * 1000))
        fc.append(f"[{i}:a]aresample=48000,adelay={delay}|{delay}[a{i}]")
        tags.append(f"[a{i}]")
    fc.append("".join(tags) +
              f"amix=inputs={len(tags)}:normalize=0:dropout_transition=0[mix]")
    fc.append(f"[mix]apad,atrim=0:{total:.3f},asetpts=N/SR/TB,{AUDIO_FILTER}[out]")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:a", "aac", "-b:a", "192k", str(dest)])
    return dest


# ---------------------------------------------------------------- главно


def read_input():
    for i, a in enumerate(sys.argv):
        if a == "--json" and i + 1 < len(sys.argv):
            return json.loads(Path(sys.argv[i + 1]).read_text(encoding="utf-8"))
    titles, images = [], []
    for n in range(1, 11):
        t = os.environ.get(f"NV_TITLE{n}", "")
        u = os.environ.get(f"NV_IMAGE{n}", "")
        if t and u:
            titles.append(t)
            images.append(u)
    return {"titles": titles, "images": images}


def main():
    data = read_input()
    pairs = [
        (clean_title(t), u)
        for t, u in zip(data.get("titles", []), data.get("images", []))
        if clean_title(t) and u
    ]
    if not pairs:
        raise SystemExit("няма нито една новина с заглавие и снимка")
    titles = [t for t, _ in pairs]
    images = [u for _, u in pairs]
    log(f"новини: {len(titles)}")

    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    font = pick_font()
    log(f"шрифт: {font}")

    logo = fetch(LOGO_URL, WORK / "logo.png")
    banner_ad = fetch(BANNER_AD_URL, WORK / "banner_ad.png") if BANNER_AD_URL else None
    banner_site = fetch(BANNER_SITE_URL, WORK / "banner_site.png")
    photos = [
        fetch(u, WORK / f"photo{i + 1}{Path(u).suffix or '.jpg'}")
        for i, u in enumerate(images)
    ]

    panel = make_logo_panel(logo, WORK / "panel.png")
    cards = [
        make_title_card(t, font, WORK / f"card{i + 1}.png")
        for i, t in enumerate(titles)
    ]

    speeches = [synth(t, WORK / f"voice{i + 1}.mp3") for i, t in enumerate(titles)]
    outro = synth(
        os.environ.get("NV_OUTRO_TEXT",
                       "Всички новини на Нова Варна прочетете тук:"),
        WORK / "voice_outro.mp3",
    )

    durations = [LEAD_IN + sp.duration + TAIL for sp in speeches]
    durations.append(OUTRO_DURATION)

    ad_index = AD_SCENE if (banner_ad and 0 <= AD_SCENE < len(photos)) else -1
    scenes = []
    for i, (photo, card, dur) in enumerate(zip(photos, cards, durations)):
        scenes.append(render_news_scene(
            photo, panel, card, banner_ad if i == ad_index else None,
            dur, WORK / f"scene{i + 1}.mp4"))
    mosaic = make_outro_mosaic(photos, WORK / "mosaic.jpg")
    glow = make_glow(WORK / "glow.png")
    scenes.append(render_outro_scene(
        logo, banner_site, mosaic, glow, OUTRO_DURATION,
        WORK / "scene_outro.mp4"))

    starts, total = stitch_video(scenes, durations, WORK / "video.mp4")
    log(f"сцени: {['%.2f' % s for s in starts]}, общо {total:.2f}s")

    leadins = [LEAD_IN] * len(speeches) + [OUTRO_LEAD_IN]
    audio = build_audio(speeches + [outro], starts, leadins, total,
                        WORK / "audio.m4a")

    final = OUT / "clip.mp4"
    vfilter = "[0:v]null[v]"
    if SUBTITLES:
        blocks = [(starts[i] + LEAD_IN, sp) for i, sp in enumerate(speeches)]
        blocks.append((starts[-1] + OUTRO_LEAD_IN, outro))
        ass = build_ass(blocks, font, WORK / "subs.ass")
        arg = str(ass).replace("\\", "/").replace(":", r"\:")
        vfilter = f"[0:v]subtitles='{arg}':fontsdir={Path(font).parent}[v]"

    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(WORK / "video.mp4"), "-i", str(audio),
         "-filter_complex", vfilter, "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "copy",
         "-movflags", "+faststart", "-shortest", str(final)])

    meta = {"duration": round(probe_duration(final), 2),
            "bytes": final.stat().st_size,
            "scenes": len(scenes), "title": titles[0]}
    (OUT / "clip.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"готово: {final} — {meta}")


if __name__ == "__main__":
    main()
