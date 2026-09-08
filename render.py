#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Нова Варна — дневен вертикален клип.

Прави същия клип като json2video шаблона, но локално с ffmpeg:
  сцена 1..3 -> снимка на новина + лого + глас + субтитри
  сцена 2    -> рекламен банер долу (с плъзгане нагоре)
  сцена 4    -> финален кадър: въртящо се лого + банер novavarna.net + глас

Гласът се прави с edge-tts (bg-BG-BorislavNeural, безплатно).
Субтитрите се сглобяват от ТОЧНИТЕ думи и тайминги, които връща edge-tts,
а не от разпознаване на реч — затова "novavarna.net" винаги излиза правилно.

Вход: променливи на средата (виж ENV по-долу) или --json файл.
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
BG_COLOR = "0x0B1424"          # тъмносиньото на кадъра
DIM_COLOR = "0x091222"         # слоят, който затъмнява снимката
DIM_ALPHA = 0.74
XFADE = 0.5                    # преход между сцените, секунди

LEAD_IN = 0.40                 # тишина преди гласа в новинарските сцени
TAIL = 0.90                    # тишина след гласа
OUTRO_LEAD_IN = 0.60
OUTRO_DURATION = 6.0

VOICE = os.environ.get("NV_VOICE", "bg-BG-BorislavNeural")
VOICE_RATE = os.environ.get("NV_VOICE_RATE", "-6%")     # малко по-бавно = по-ясно
VOICE_PITCH = os.environ.get("NV_VOICE_PITCH", "+0Hz")
VOICE_VOLUME = os.environ.get("NV_VOICE_VOLUME", "+0%")
# изравняване на силата на звука по стандарта за социални мрежи
AUDIO_FILTER = os.environ.get(
    "NV_AUDIO_FILTER",
    "highpass=f=80,"
    "equalizer=f=2600:width_type=o:width=1.4:g=3,"   # присъствие на съгласните
    "acompressor=threshold=-18dB:ratio=3:attack=8:release=180,"
    "loudnorm=I=-15:TP=-1.5:LRA=9,"
    "alimiter=limit=0.94",
)
OUTRO_TEXT = os.environ.get(
    "NV_OUTRO_TEXT", "Всички новини на Нова Варна прочетете тук:"
)

# Може да е локален файл в хранилището или адрес в интернет.
LOGO_URL = os.environ.get("NV_LOGO_URL", "assets/logo.png")
BANNER_AD_URL = os.environ.get(
    "NV_BANNER_AD_URL",
    "https://novavarna.net/wp-content/uploads/2026/09/banner_reklama.png",
)
BANNER_SITE_URL = os.environ.get(
    "NV_BANNER_SITE_URL",
    "https://novavarna.net/wp-content/uploads/2026/09/banner_novavarna.png",
)

# позиции, същите като в json2video шаблона
LOGO_X, LOGO_Y, LOGO_W = 60, 110, 560
PHOTO_Y = 460
PHOTO_W = 1080                 # снимката се вписва в тази рамка
PHOTO_MAX_H = 1000
OUTRO_LOGO_W = 840             # логото на финалния кадър
OUTRO_LOGO_CY = 800            # център по вертикала
OUTRO_SPINS = 1.0              # колко пълни оборота прави логото
OUTRO_SPIN_TIME = 2.4          # за колко секунди ги прави и спира изправено
BANNER_X, BANNER_Y, BANNER_W = 60, 1560, 960
BANNER_RISE = 0.55             # колко трае плъзгането нагоре
BANNER_AD_START = 1.20         # кога влиза рекламният банер
BANNER_SITE_START = 2.60       # кога влиза банерът novavarna.net (след "тук:")

SUB_Y = 1440
SUB_SIZE = 64
SUB_MAX_WORDS = 4
SUB_FILL = "&H00FFFFFF"        # бяло  (ASS е &HAABBGGRR)
SUB_ACTIVE = "&H00BD7941"      # #4179BD
SUB_OUTLINE = "&H001F1208"     # #08121F

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Black.ttf",
    "/usr/share/fonts/truetype/roboto/hinted/Roboto-Black.ttf",
    "/usr/share/fonts/truetype/google-fonts/Roboto-Black.ttf",
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
    for p in FONT_CANDIDATES:
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
    """Маха HTML кодовете, които WordPress връща, и излишните интервали."""
    t = html.unescape(raw or "")
    t = re.sub(r"<[^>]+>", "", t)
    t = unicodedata.normalize("NFC", t)
    t = t.replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


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


def synth(text: str, dest: Path) -> Speech:
    words = asyncio.run(_synth(text, dest))
    dur = probe_duration(dest)
    log(f"глас: {dur:.2f}s, {len(words)} думи — {text[:60]}")
    return Speech(dest, dur, words)


# ---------------------------------------------------------------- субтитри


def ass_time(t: float) -> str:
    t = max(t, 0)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def ass_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build_ass(blocks, font_path: str, dest: Path):
    """
    blocks: списък от (offset_в_клипа, Speech)
    Прави ASS, в който активната дума е синя, а редът е до SUB_MAX_WORDS думи.
    """
    font_name = Path(font_path).stem
    head = f"""[Script Info]
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
"""
    lines = [head]

    for offset, sp in blocks:
        words = [w for w in sp.words if w.text.strip()]
        if not words:
            continue
        for i in range(0, len(words), SUB_MAX_WORDS):
            group = words[i : i + SUB_MAX_WORDS]
            is_last_group = i + SUB_MAX_WORDS >= len(words)
            for j, w in enumerate(group):
                start = offset + w.start
                if j + 1 < len(group):
                    end = offset + group[j + 1].start
                elif not is_last_group:
                    end = offset + words[i + SUB_MAX_WORDS].start
                else:
                    end = offset + max(w.end, sp.duration)
                if end <= start:
                    end = start + 0.12
                parts = []
                for k, g in enumerate(group):
                    txt = ass_escape(g.text)
                    if k == j:
                        parts.append(
                            f"{{\\c{SUB_ACTIVE}}}{txt}{{\\c{SUB_FILL}}}"
                        )
                    else:
                        parts.append(txt)
                body = "{\\pos(%d,%d)}" % (W // 2, SUB_Y) + " ".join(parts)
                lines.append(
                    f"Dialogue: 0,{ass_time(start)},{ass_time(end)},NV,,0,0,0,,{body}"
                )

    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"субтитри: {len(lines) - 1} реплики -> {dest}")
    return dest


# ---------------------------------------------------------------- сцени


def rise_expr(y_final: int, start: float, rise: float) -> str:
    """
    Плъзгане отдолу нагоре с плавно спиране (cubic ease-out).
    Извън екрана е на H, крайната позиция е y_final.
    """
    travel = H - y_final
    p = f"min(max((t-{start:.2f})/{rise:.2f},0),1)"
    ease = f"(1-pow(1-{p},3))"
    return f"'{H}-{travel}*{ease}'"


def render_news_scene(photo: Path, logo: Path, banner, duration: float, dest: Path):
    inputs = [
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(photo),
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(logo),
    ]
    if banner:
        inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(banner)]

    fc = [
        # фон: снимката, увеличена двойно, изрязана до кадъра, потъмнена
        f"[0:v]scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},scale={W}:{H},eq=brightness=-0.25:saturation=0.80,"
        f"setsar=1[bg]",
        # полупрозрачният слой, който в json2video никога не се получаваше
        f"color=c={DIM_COLOR}:s={W}x{H}:d={duration:.3f}:r={FPS},"
        f"format=rgba,colorchannelmixer=aa={DIM_ALPHA}[dim]",
        "[bg][dim]overlay=0:0[bgd]",
        # самата снимка, вписана в рамка, за да не изяде целия кадър
        f"[0:v]scale=w='if(gt(a,{PHOTO_W}/{PHOTO_MAX_H}),{PHOTO_W},-2)':"
        f"h='if(gt(a,{PHOTO_W}/{PHOTO_MAX_H}),-2,{PHOTO_MAX_H})',setsar=1[photo]",
        f"[bgd][photo]overlay=x=(W-w)/2:y={PHOTO_Y}[v1]",
        # логото горе вляво
        f"[1:v]scale={LOGO_W}:-1,setsar=1[logo]",
        f"[v1][logo]overlay=x={LOGO_X}:y={LOGO_Y}[v2]",
    ]
    last = "v2"
    if banner:
        fc.append(f"[2:v]scale={BANNER_W}:-1,setsar=1[ban]")
        fc.append(
            f"[{last}][ban]overlay=x={BANNER_X}:"
            f"y={rise_expr(BANNER_Y, BANNER_AD_START, BANNER_RISE)}[v3]"
        )
        last = "v3"
    fc.append(f"[{last}]fps={FPS},format=yuv420p[out]")

    run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-t", f"{duration:.3f}", str(dest)]
    )
    return dest


def render_outro_scene(logo: Path, banner: Path, duration: float, dest: Path):
    # логото се върти, затова първо го слагаме в квадрат, за да не се реже
    box = int(OUTRO_LOGO_W * 1.15) // 2 * 2
    # един пълен оборот, който плавно спира, за да стои логото право накрая
    p = f"min(t/{OUTRO_SPIN_TIME:.2f},1)"
    angle = f"{2 * 3.14159265 * OUTRO_SPINS:.5f}*(1-pow(1-{p},3))"
    fc = [
        f"color=c={BG_COLOR}:s={W}x{H}:d={duration:.3f}:r={FPS},setsar=1[bg]",
        f"[0:v]scale={OUTRO_LOGO_W}:-1,setsar=1,"
        f"pad={box}:{box}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"format=rgba,rotate=a='{angle}':c=black@0:ow={box}:oh={box}[logo]",
        f"[bg][logo]overlay=x={(W - box)//2}:y={OUTRO_LOGO_CY - box//2}[v1]",
        f"[1:v]scale={BANNER_W}:-1,setsar=1[ban]",
        f"[v1][ban]overlay=x={BANNER_X}:"
        f"y={rise_expr(BANNER_Y, BANNER_SITE_START, BANNER_RISE)}[v2]",
        f"[v2]fps={FPS},format=yuv420p[out]",
    ]
    run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-loop", "1", "-t", f"{duration:.3f}", "-i", str(logo),
         "-loop", "1", "-t", f"{duration:.3f}", "-i", str(banner),
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-t", f"{duration:.3f}", str(dest)]
    )
    return dest


def stitch_video(scenes, durations, dest: Path):
    """Слепва сцените с кръстосано затихване и връща началата им в клипа."""
    starts = [0.0]
    for d in durations[:-1]:
        starts.append(starts[-1] + d - XFADE)

    inputs = []
    for s in scenes:
        inputs += ["-i", str(s)]

    fc = []
    last = "0:v"
    elapsed = durations[0]
    for i in range(1, len(scenes)):
        offset = elapsed - XFADE
        tag = f"x{i}"
        fc.append(
            f"[{last}][{i}:v]xfade=transition=fade:duration={XFADE}:"
            f"offset={offset:.3f}[{tag}]"
        )
        last = tag
        elapsed = offset + durations[i]

    fc.append(f"[{last}]format=yuv420p[out]")
    run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(dest)]
    )
    return starts, elapsed


def build_audio(speeches, starts, leadins, total: float, dest: Path):
    inputs = []
    for sp in speeches:
        inputs += ["-i", str(sp.path)]
    fc = []
    tags = []
    for i, (sp, st, lead) in enumerate(zip(speeches, starts, leadins)):
        delay = int(round((st + lead) * 1000))
        fc.append(f"[{i}:a]aresample=48000,adelay={delay}|{delay}[a{i}]")
        tags.append(f"[a{i}]")
    fc.append(
        "".join(tags) + f"amix=inputs={len(tags)}:normalize=0:dropout_transition=0[mix]"
    )
    fc.append(
        f"[mix]apad,atrim=0:{total:.3f},asetpts=N/SR/TB,{AUDIO_FILTER}[out]"
    )
    run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(fc), "-map", "[out]",
         "-c:a", "aac", "-b:a", "192k", str(dest)]
    )
    return dest


# ---------------------------------------------------------------- главно


def read_input():
    for i, a in enumerate(sys.argv):
        if a == "--json" and i + 1 < len(sys.argv):
            return json.loads(Path(sys.argv[i + 1]).read_text(encoding="utf-8"))
    data = {
        "titles": [
            os.environ.get("NV_TITLE1", ""),
            os.environ.get("NV_TITLE2", ""),
            os.environ.get("NV_TITLE3", ""),
        ],
        "images": [
            os.environ.get("NV_IMAGE1", ""),
            os.environ.get("NV_IMAGE2", ""),
            os.environ.get("NV_IMAGE3", ""),
        ],
    }
    return data


def main():
    data = read_input()
    titles = [clean_title(t) for t in data.get("titles", []) if clean_title(t)]
    images = [u for u in data.get("images", []) if u]
    if not titles or len(titles) != len(images):
        raise SystemExit(
            f"трябват еднакъв брой заглавия и снимки (получих {len(titles)} и {len(images)})"
        )

    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    font = pick_font()
    log(f"шрифт: {font}")

    logo = fetch(LOGO_URL, WORK / "logo.png")
    banner_ad = fetch(BANNER_AD_URL, WORK / "banner_ad.png")
    banner_site = fetch(BANNER_SITE_URL, WORK / "banner_site.png")
    photos = [
        fetch(u, WORK / f"photo{i+1}{Path(u).suffix or '.jpg'}")
        for i, u in enumerate(images)
    ]

    speeches = [synth(t, WORK / f"voice{i+1}.mp3") for i, t in enumerate(titles)]
    outro = synth(OUTRO_TEXT, WORK / "voice_outro.mp3")

    durations = [LEAD_IN + sp.duration + TAIL for sp in speeches]
    durations.append(OUTRO_DURATION)

    scenes = []
    for i, (photo, dur) in enumerate(zip(photos, durations)):
        banner = banner_ad if i == 1 else None
        scenes.append(
            render_news_scene(photo, logo, banner, dur, WORK / f"scene{i+1}.mp4")
        )
    scenes.append(
        render_outro_scene(logo, banner_site, OUTRO_DURATION, WORK / "scene_outro.mp4")
    )

    starts, total = stitch_video(scenes, durations, WORK / "video.mp4")
    log(f"сцени започват на {['%.2f' % s for s in starts]}, общо {total:.2f}s")

    leadins = [LEAD_IN] * len(speeches) + [OUTRO_LEAD_IN]
    audio = build_audio(
        speeches + [outro], starts, leadins, total, WORK / "audio.m4a"
    )

    blocks = [
        (starts[i] + LEAD_IN, sp) for i, sp in enumerate(speeches)
    ] + [(starts[-1] + OUTRO_LEAD_IN, outro)]
    ass = build_ass(blocks, font, WORK / "subs.ass")

    final = OUT / "clip.mp4"
    subs_arg = str(ass).replace("\\", "/").replace(":", r"\:")
    run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(WORK / "video.mp4"), "-i", str(audio),
         "-filter_complex",
         f"[0:v]subtitles='{subs_arg}':fontsdir={Path(font).parent}[v]",
         "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "copy",
         "-movflags", "+faststart", "-shortest", str(final)]
    )

    meta = {
        "duration": round(probe_duration(final), 2),
        "bytes": final.stat().st_size,
        "scenes": len(scenes),
        "title": titles[0],
    }
    (OUT / "clip.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"готово: {final} — {meta}")


if __name__ == "__main__":
    main()
