import os
import json
import hashlib
import argparse
import subprocess
from pathlib import Path
import random
import math
import re

import requests
from PIL import Image, ImageDraw, ImageFont
from PIL import ImageFilter


ROOT = Path(__file__).resolve().parent
RENDER_SETTINGS_JSON = ROOT / "input" / "render_settings.json"

CONFIG = {
    "w": 1080,
    "h": 1920,
    "fps": 30,
    "timer_seconds": 3.0,
    "timer_type": "classic_bar",
    "timer_countdown_start": 4,
    "timer_circle_radius": 68,
    "timer_circle_dot_size": 10,
    "timer_circle_y_offset": 30,
    "timer_circle_font_size": 66,
    "question_entry_effect": "fade",
    "choices_entry_effect": "fade",
    "question_slide_seconds": 0.8,
    "colors": {
        "bg": (255, 200, 220),
        "card": (255, 255, 255),
        "card_outline": (255, 255, 255, 50),
        "text": (255, 255, 255),
        "muted": (200, 200, 200),
        "timer_track": (255, 210, 230, 255),
        "timer_fill": (255, 118, 152, 255),
        "timer_border": (255, 255, 255, 255),
    },
    "background_image": "bg.png",
    "layout": {
        "timer": {"x": 120, "y": 90, "w": 840, "h": 22},
        "question_box": {"x": 80, "y": 220, "w": 920, "h": 320},
        "single_image": {
            "question_box": {"x": 80, "y": 220, "w": 920, "h": 300},
            "image_box": {"x": 220, "y": 600, "w": 640, "h": 700},
        },
        "choices": {
            "a": {
                "label_box": {"x": 180, "y": 540, "w": 720, "h": 95},
                "image_box": {"x": 180, "y": 645, "w": 720, "h": 300},
            },
            "b": {
                "label_box": {"x": 180, "y": 1020, "w": 720, "h": 130},
                "image_box": {"x": 180, "y": 1160, "w": 720, "h": 600},
            },
        },
    },
    "timer_smooth_factor": 6,
    "timer_max_steps": 320,
    "clock_trim_seconds": 1.5,
    "text_effects": {
        "fill": (255, 255, 255),
        "stroke_fill": (0, 0, 0),
        "stroke_width_question": 6,
        "stroke_width_choice": 5,
        # 40-50% black shadow approximation over pastel background.
        "shadow_fill": (120, 100, 110),
        "shadow_offset": (3, 3),
    },
    # Reveal choices slightly before the question audio fully ends.
    "choices_lead_seconds": 2.8,
    "choices_slide_seconds": 0.35,
    "choices_slide_px": 120,
    "transition_sound": "",
    "transition_sound_volume": 0.35,
    # Transition between questions (seconds). Set 0 to disable.
    "transition_seconds": 0.25,
    "transition_type": "fade",
    # Special transition around ad clip (VIDEOARAREKLAM.mp4)
    "ad_transition_type": "slideleft",
    "ad_transition_seconds": 0.35,
    # Voice (question) audio gain
    "voice_gain_db": 7.0,
    # Final AAC bitrate (higher = better quality, larger file size).
    "audio_bitrate_kbps": 192,
    # When user provides custom question audio (mp3/wav/etc), we don't know
    # its loudness level; applying default gain can cause clipping.
    # Set to 0.0 to keep the source as-is.
    "custom_audio_gain_db": 0.0,
    "font_family": "rubik",
    "elevenlabs": {
        "api_url": "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        "model_id_default": "eleven_multilingual_v2",
        "stability": 0.45,
        "similarity_boost": 0.75,
        "style": 0.0,
    },
}


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ffprobe_duration_seconds(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.check_output(cmd).decode("utf-8").strip()
    return float(out)


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def concat_with_transitions(segment_paths, out_path: Path, transition_seconds: float, transition_type: str):
    if len(segment_paths) == 0:
        raise RuntimeError("No segments to concat")

    if len(segment_paths) == 1 or transition_seconds <= 0:
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(segment_paths[0]),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:a",
                f"{int(CONFIG.get('audio_bitrate_kbps', 192))}k",
                "-pix_fmt",
                "yuv420p",
                str(out_path),
            ]
        )
        return []

    durs = [ffprobe_duration_seconds(Path(p)) for p in segment_paths]

    inputs = []
    for p in segment_paths:
        inputs.extend(["-i", str(p)])

    fc = []
    for i in range(len(segment_paths)):
        fc.append(f"[{i}:v]fps={CONFIG['fps']},format=yuv420p[v{i}]")
        fc.append(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=mono[a{i}]")

    cur_v = "v0"
    cur_a = "a0"
    # Duration of current chained output timeline.
    current_timeline = durs[0]
    ad_transition_type = str(CONFIG.get("ad_transition_type", "slideleft"))
    ad_transition_seconds = float(CONFIG.get("ad_transition_seconds", transition_seconds))
    q_to_q_offsets = []

    for i in range(1, len(segment_paths)):
        nxt_v = f"v{i}"
        nxt_a = f"a{i}"
        out_v = f"vx{i}"
        out_a = f"ax{i}"

        # Special transition only:
        # - right after intro
        # - before and after ad
        left_name = Path(segment_paths[i - 1]).name.lower()
        right_name = Path(segment_paths[i]).name.lower()
        is_intro_edge = "_intro_norm" in left_name
        is_ad_edge = ("_ad_norm" in left_name) or ("_ad_norm" in right_name)
        is_special_edge = is_intro_edge or is_ad_edge
        t_type = ad_transition_type if is_special_edge else transition_type
        t_dur = ad_transition_seconds if is_special_edge else transition_seconds

        # xfade offset is relative to the current chained output timeline.
        off = max(0.0, current_timeline - t_dur)

        fc.append(f"[{cur_v}][{nxt_v}]xfade=transition={t_type}:duration={t_dur}:offset={off}[{out_v}]")
        fc.append(f"[{cur_a}][{nxt_a}]acrossfade=d={t_dur}[{out_a}]")
        if re.fullmatch(r"q\d+\.mp4", left_name) and re.fullmatch(r"q\d+\.mp4", right_name):
            q_to_q_offsets.append(off)
        cur_v = out_v
        cur_a = out_a
        current_timeline = current_timeline + durs[i] - t_dur

    filter_complex = ";".join(fc)
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{cur_v}]",
        "-map",
        f"[{cur_a}]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-b:a",
        f"{int(CONFIG.get('audio_bitrate_kbps', 192))}k",
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    run(cmd)
    return q_to_q_offsets


def normalize_clip_to_standard(in_path: Path, out_path: Path):
    """
    Normalize any input clip to our standard output format:
    - 1080x1920 (center crop or pad via scale+pad)
    - fps = CONFIG["fps"]
    - yuv420p
    - audio mono 44100 (if present)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w = int(CONFIG["w"])
    h = int(CONFIG["h"])
    fps = int(CONFIG["fps"])
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},format=yuv420p"
    )
    # -shortest keeps things safe if audio/video lengths mismatch after processing
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(in_path),
            "-vf",
            vf,
            "-af",
            "aresample=44100,aformat=channel_layouts=mono",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-b:a",
            f"{int(CONFIG.get('audio_bitrate_kbps', 192))}k",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(out_path),
        ]
    )


def add_background_music(video_in: Path, bgm_in: Path, video_out: Path, bgm_volume: float):
    if (not bgm_in.exists()) or bgm_volume <= 0:
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_in),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                str(video_out),
            ]
        )
        return

    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_in),
            "-i",
            str(bgm_in),
            "-filter_complex",
            f"[1:a]volume={bgm_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            f"{int(CONFIG.get('audio_bitrate_kbps', 192))}k",
            str(video_out),
        ]
    )


def add_transition_sfx(video_in: Path, sfx_in: Path, video_out: Path, trigger_offsets, sfx_volume: float):
    if (not sfx_in.exists()) or sfx_volume <= 0 or (not trigger_offsets):
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_in),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                str(video_out),
            ]
        )
        return

    offsets = [max(0.0, float(x)) for x in trigger_offsets]
    n = len(offsets)
    split_labels = [f"sfx{i}" for i in range(n)]
    delayed_labels = [f"sfxd{i}" for i in range(n)]

    fc = [
        f"[1:a]aformat=sample_rates=44100:channel_layouts=mono,volume={sfx_volume},asplit={n}" + "".join([f"[{lbl}]" for lbl in split_labels])
    ]
    for i, off in enumerate(offsets):
        ms = int(round(off * 1000))
        fc.append(f"[{split_labels[i]}]adelay={ms}|{ms}[{delayed_labels[i]}]")
    mix_inputs = "[0:a]" + "".join([f"[{lbl}]" for lbl in delayed_labels])
    fc.append(f"{mix_inputs}amix=inputs={n + 1}:duration=first:dropout_transition=0[a]")

    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_in),
            "-i",
            str(sfx_in),
            "-filter_complex",
            ";".join(fc),
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            f"{int(CONFIG.get('audio_bitrate_kbps', 192))}k",
            str(video_out),
        ]
    )


def get_font(size: int, bold: bool = False):
    font_family = str(CONFIG.get("font_family", "rubik")).strip().lower()
    primary = []
    if font_family == "brlns":
        primary = [
            ROOT / ("BRLNSB.TTF" if bold else "BRLNSR.TTF"),
            ROOT / ("BRLNSR.TTF" if bold else "BRLNSB.TTF"),
        ]
    else:
        primary = [
            ROOT / "Rubik-VariableFont_wght.ttf",
            ROOT / "Rubik-Italic-VariableFont_wght.ttf",
        ]

    candidates = [
        *primary,
        ROOT / "assets" / "fonts" / "font.ttf",
        ROOT / ("BRLNSB.TTF" if bold else "BRLNSR.TTF"),
        ROOT / ("BRLNSR.TTF" if bold else "BRLNSB.TTF"),
        ROOT / "Rubik-VariableFont_wght.ttf",
        ROOT / "Rubik-Italic-VariableFont_wght.ttf",
        Path(r"C:\Windows\Fonts\LuckiestGuy-Regular.ttf"),
        Path(r"C:\Windows\Fonts\LuckiestGuy.ttf"),
        Path(r"C:\Windows\Fonts\Montserrat-ExtraBold.ttf"),
        Path(r"C:\Windows\Fonts\Montserrat-ExtraBold.otf"),
        Path(r"C:\Windows\Fonts\FredokaOne-Regular.ttf"),
        Path(r"C:\Windows\Fonts\FredokaOne.ttf"),
        Path(r"C:\Windows\Fonts\KomikaAxis.ttf"),
        Path(r"C:\Windows\Fonts\KomikaAxis.otf"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\calibrib.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\calibri.ttf"),
        Path(r"C:\Windows\Fonts\verdana.ttf"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return ImageFont.truetype(str(p), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def get_ffmpeg_fontfile() -> str:
    font_family = str(CONFIG.get("font_family", "rubik")).strip().lower()
    candidates = []
    if font_family == "brlns":
        candidates.extend([ROOT / "BRLNSB.TTF", ROOT / "BRLNSR.TTF"])
    else:
        candidates.extend([ROOT / "Rubik-VariableFont_wght.ttf", ROOT / "Rubik-Italic-VariableFont_wght.ttf"])
    candidates.extend(
        [
            ROOT / "BRLNSB.TTF",
            ROOT / "BRLNSR.TTF",
            ROOT / "Rubik-VariableFont_wght.ttf",
            ROOT / "Rubik-Italic-VariableFont_wght.ttf",
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path(r"C:\Windows\Fonts\arial.ttf"),
        ]
    )
    for p in candidates:
        if p.exists():
            return str(p).replace("\\", "/").replace(":", "\\:")
    return "Arial"


def wrap_text(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, text: str, max_width: int):
    words = text.split()
    if not words:
        return [""]
    lines = []
    line = words[0]
    for w in words[1:]:
        test = line + " " + w
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            line = test
        else:
            lines.append(line)
            line = w
    lines.append(line)
    return lines


def sanitize_ascii_display(text: str) -> str:
    # Turkish characters can render oddly depending on the font.
    # Convert them to ASCII equivalents for more reliable output.
    mapping = {
        "ç": "c",
        "Ç": "C",
        "ğ": "g",
        "Ğ": "G",
        "ı": "i",
        "İ": "I",
        "ö": "o",
        "Ö": "O",
        "ş": "s",
        "Ş": "S",
        "ü": "u",
        "Ü": "U",
    }
    return "".join(mapping.get(ch, ch) for ch in text)


def draw_wrapped(
    draw,
    font,
    box,
    text,
    fill,
    align="center",
    line_spacing=6,
    stroke_width=0,
    stroke_fill=None,
    shadow_offset=(0, 0),
    shadow_fill=None,
):
    x1, y1, x2, y2 = box
    max_width = x2 - x1
    lines = wrap_text(draw, font, text, max_width)
    bboxes = [draw.textbbox((0, 0), ln if ln else " ", font=font) for ln in lines]
    line_heights = [(bb[3] - bb[1]) for bb in bboxes]
    total_h = sum(line_heights) + (len(lines) - 1) * line_spacing
    y = y1 + max(0, ((y2 - y1) - total_h) // 2)
    for i, ln in enumerate(lines):
        bbox = draw.textbbox((0, 0), ln if ln else " ", font=font)
        w = bbox[2] - bbox[0]
        if align == "center":
            tx = x1 + (max_width - w) // 2
        else:
            tx = x1
        if shadow_fill is not None and shadow_offset != (0, 0):
            sx, sy = shadow_offset
            draw.text((tx + sx, y + sy), ln, font=font, fill=shadow_fill)

        if stroke_width > 0 and stroke_fill is not None:
            draw.text(
                (tx, y),
                ln,
                font=font,
                fill=fill,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
            )
        else:
            draw.text((tx, y), ln, font=font, fill=fill)
        y += line_heights[i] + line_spacing


def make_minimal_background():
    w, h = CONFIG["w"], CONFIG["h"]
    bg_path = ROOT / str(CONFIG.get("background_image", "bg.png"))
    if bg_path.exists():
        im = Image.open(bg_path).convert("RGB")
        im_w, im_h = im.size
        # Cover fit (aspect preserved), center crop.
        scale = max(w / im_w, h / im_h)
        new_w = max(1, int(round(im_w * scale)))
        new_h = max(1, int(round(im_h * scale)))
        im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        im = im.crop((left, top, left + w, top + h))
        return im

    return Image.new("RGB", (w, h), CONFIG["colors"]["bg"])


def ensure_placeholder_image(path: Path, size, label: str):
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, CONFIG["colors"]["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(255, 255, 255), width=4)
    font = get_font(int(size[1] * 0.11))
    bb = d.textbbox((0, 0), label, font=font)
    w = bb[2] - bb[0]
    d.text(((size[0] - w) // 2, (size[1] - bb[3] + bb[1]) // 2), label, font=font, fill=(255, 255, 255))
    img.save(path)


def build_base_image(
    out_png: Path,
    question_layer_png: Path,
    choices_layer_png: Path,
    question: str,
    layout_type: str,
    a_text: str,
    b_text: str,
    a_img: Path,
    b_img: Path,
    single_img: Path,
):
    w, h = CONFIG["w"], CONFIG["h"]
    img = make_minimal_background()
    draw = ImageDraw.Draw(img)

    # Question is rendered into a transparent layer so we can animate it in ffmpeg.
    question_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    qdraw = ImageDraw.Draw(question_layer)

    font_q = get_font(56)
    font_c = get_font(48)
    font_label = get_font(44)

    question_box = CONFIG["layout"]["question_box"]
    if layout_type == "single_image":
        question_box = CONFIG["layout"]["single_image"]["question_box"]
    draw_wrapped(
        qdraw,
        font_q,
        (
            question_box["x"],
            question_box["y"],
            question_box["x"] + question_box["w"],
            question_box["y"] + question_box["h"],
        ),
        question,
        CONFIG["colors"]["text"],
        align="center",
        line_spacing=16,
        stroke_width=CONFIG["text_effects"]["stroke_width_question"],
        stroke_fill=CONFIG["text_effects"]["stroke_fill"],
        shadow_fill=CONFIG["text_effects"]["shadow_fill"],
        shadow_offset=CONFIG["text_effects"]["shadow_offset"],
    )

    def render_choice_text(draw_obj, choice, letter: str, text: str, font):
        lb = choice["label_box"]
        label_text = f"{letter}) {text}".strip()

        draw_wrapped(
            draw_obj,
            font,
            (lb["x"], lb["y"], lb["x"] + lb["w"], lb["y"] + lb["h"]),
            label_text,
            CONFIG["colors"]["text"],
            align="center",
            line_spacing=6,
            stroke_width=CONFIG["text_effects"]["stroke_width_choice"],
            stroke_fill=CONFIG["text_effects"]["stroke_fill"],
            shadow_fill=CONFIG["text_effects"]["shadow_fill"],
            shadow_offset=CONFIG["text_effects"]["shadow_offset"],
        )

    def render_choice_image(layer_img: Image.Image, choice, letter: str, img_path: Path):
        ib = choice["image_box"]
        iw = int(ib["w"])
        ih = int(ib["h"])
        ensure_placeholder_image(img_path, (iw, ih), letter)

        source = Image.open(img_path).convert("RGBA")
        bg = Image.new("RGBA", source.size, (*CONFIG["colors"]["bg"], 255))
        im = Image.alpha_composite(bg, source).convert("RGB")
        im_w, im_h = im.size
        # Kutuya "cover" gibi oturt: oran bozulmasın, merkezden kırpılsın.
        scale = max(iw / im_w, ih / im_h)
        new_w = max(1, int(round(im_w * scale)))
        new_h = max(1, int(round(im_h * scale)))
        im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - iw) // 2
        top = (new_h - ih) // 2
        im = im.crop((left, top, left + iw, top + ih))
        layer_img.paste(im, (int(ib["x"]), int(ib["y"])))

    # Choices are rendered into a single transparent layer so we can reveal them together.
    choices_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cdv = ImageDraw.Draw(choices_layer)

    if layout_type == "single_image":
        single_choice = CONFIG["layout"]["single_image"]["image_box"]
        render_choice_image(
            choices_layer,
            {"image_box": single_choice},
            "",
            single_img,
        )
    else:
        choices = CONFIG["layout"]["choices"]
        a_choice = choices["a"]
        b_choice = choices["b"]

        font_choice_a = get_font(52)
        font_choice_b = get_font(56)

        render_choice_text(cdv, a_choice, "A", a_text, font_choice_a)
        render_choice_image(choices_layer, a_choice, "A", a_img)
        render_choice_text(cdv, b_choice, "B", b_text, font_choice_b)
        render_choice_image(choices_layer, b_choice, "B", b_img)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    question_layer.save(question_layer_png)
    choices_layer.save(choices_layer_png)


def make_mock_wav(voice_text: str, out_wav: Path):
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if out_wav.exists() and out_wav.stat().st_size > 0:
        return

    seconds = 1.5 + (len(voice_text) / 45.0)
    seconds = max(1.5, min(9.0, seconds))

    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


def mock_mp3_to_wav(mock_mp3: Path, out_wav: Path):
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if out_wav.exists() and out_wav.stat().st_size > 0:
        return
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(mock_mp3),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


def convert_any_audio_to_wav(in_audio: Path, out_wav: Path):
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(in_audio),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


def elevenlabs_tts_to_wav(
    voice_id: str,
    model_id: str,
    voice_text: str,
    out_wav: Path,
    mock: bool = False,
    mock_mp3=None,
):
    if mock:
        if mock_mp3 and mock_mp3.exists():
            mock_mp3_to_wav(mock_mp3, out_wav)
        else:
            make_mock_wav(voice_text, out_wav)
        return

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if out_wav.exists() and out_wav.stat().st_size > 0:
        return

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("Set env var: ELEVENLABS_API_KEY")

    url = CONFIG["elevenlabs"]["api_url"].format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/wav",
        "content-type": "application/json",
    }
    payload = {
        "text": voice_text,
        "model_id": model_id,
        "voice_settings": {
            "stability": CONFIG["elevenlabs"]["stability"],
            "similarity_boost": CONFIG["elevenlabs"]["similarity_boost"],
            "style": CONFIG["elevenlabs"]["style"],
        },
    }
    r = requests.post(url, headers=headers, json=payload, timeout=180)
    r.raise_for_status()

    tmp_raw = out_wav.with_suffix(".raw")
    tmp_raw.write_bytes(r.content)

    # Ensure WAV via ffmpeg (some accounts may return non-wav despite accept header)
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(tmp_raw),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    try:
        tmp_raw.unlink(missing_ok=True)
    except Exception:
        pass


def make_question_segment(
    base_png: Path,
    question_layer_png: Path,
    choices_layer_png: Path,
    audio_wav: Path,
    out_mp4: Path,
    audio_dur: float,
    clock_mp3,
    voice_gain_db: float,
):
    timer_seconds = float(CONFIG["timer_seconds"])
    w, h = CONFIG["w"], CONFIG["h"]
    fps = int(CONFIG["fps"])
    bar = CONFIG["layout"]["timer"]
    timer_type = str(CONFIG.get("timer_type", "classic_bar") or "classic_bar").strip().lower()

    start = float(audio_dur)
    end = start + timer_seconds
    total = end
    clock_trim_seconds = float(CONFIG.get("clock_trim_seconds", 0.0))
    clock_window_end = clock_trim_seconds + timer_seconds

    # Keep timer visuals only in the timer window.
    # Track shows from start->end (grey), fill grows during that window.
    timer_track_rgba = CONFIG["colors"]["timer_track"]
    timer_fill = CONFIG["colors"]["timer_fill"]

    def rgba_to_hex(rgb):
        return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"

    track_hex = rgba_to_hex(timer_track_rgba)
    fill_hex = rgba_to_hex(timer_fill[:3])
    border_hex = rgba_to_hex(CONFIG["colors"]["timer_border"][:3])

    bar_w = bar["w"]
    question_fade_d = float(CONFIG.get("question_fade_seconds", 0.45))
    question_slide_d = float(CONFIG.get("question_slide_seconds", 0.8))
    question_slide_px = float(CONFIG.get("question_slide_px", 140))
    question_entry_effect = str(CONFIG.get("question_entry_effect", "fade") or "fade").strip().lower()
    # Timer bar:
    # - t=0..start: dolgu 0
    # - t=start..end: dolgu genişliği lineer artsın
    # - t>end: dolgu bar_w olarak kalsın
    # drawbox'ta width ifadesi time variable ile güvenilir çalışmadığı için,
    # 50 adımda sabit genişlikli fill kutuları ile görsel animasyon üretiyoruz.
    # Smooth görünüm için frame'den daha sık parçalayıp her adımda sabit genişlik çiziyoruz.
    smooth_factor = float(CONFIG.get("timer_smooth_factor", 4))
    computed_steps = int(round(timer_seconds * fps * smooth_factor))
    max_steps = int(CONFIG.get("timer_max_steps", 200))
    steps = max(10, min(computed_steps, max_steps))
    step = timer_seconds / steps
    vfilters = []
    if timer_type == "countdown_circle":
        cx = int(bar["x"] + (bar["w"] / 2))
        cy = int(bar["y"] + (bar["h"] / 2) + int(CONFIG.get("timer_circle_y_offset", 0)))
        radius = int(CONFIG.get("timer_circle_radius", 60))
        dot_size = int(CONFIG.get("timer_circle_dot_size", 9))
        ring_segments = int(CONFIG.get("timer_circle_segments", 72))
        epsilon = 1.0 / max(100000.0, ring_segments * fps)
        for i in range(ring_segments):
            angle = (-math.pi / 2.0) + ((2.0 * math.pi) * (i / ring_segments))
            px = int(round(cx + radius * math.cos(angle) - (dot_size / 2)))
            py = int(round(cy + radius * math.sin(angle) - (dot_size / 2)))
            vfilters.append(
                f"drawbox=x={px}:y={py}:w={dot_size}:h={dot_size}:color={track_hex}:thickness=fill:enable='between(t,{start:.6f},{end:.6f})'"
            )
            t1 = start + timer_seconds * ((i + 1) / ring_segments)
            vfilters.append(
                f"drawbox=x={px}:y={py}:w={dot_size}:h={dot_size}:color={fill_hex}:thickness=fill:enable='between(t,{start:.6f},{t1 + epsilon:.6f})'"
            )
        countdown_start = int(CONFIG.get("timer_countdown_start", 4))
        countdown_start = max(1, countdown_start)
        slice_d = timer_seconds / countdown_start
        ff_font = get_ffmpeg_fontfile()
        font_size = int(CONFIG.get("timer_circle_font_size", 66))
        for n in range(countdown_start, 0, -1):
            idx = countdown_start - n
            t0 = start + (idx * slice_d)
            t1 = end if n == 1 else (start + ((idx + 1) * slice_d))
            vfilters.append(
                f"drawtext=fontfile='{ff_font}':text='{n}':x=(w-text_w)/2:y={cy - int(font_size * 0.42)}:fontsize={font_size}:fontcolor={border_hex}:borderw=2:bordercolor=black:enable='between(t,{t0:.6f},{t1:.6f})'"
            )
    else:
        vfilters.extend(
            [
                f"drawbox=x={bar['x']}:y={bar['y']}:w={bar_w}:h={bar['h']}:color={track_hex}:thickness=fill:enable='between(t,{start:.6f},{end:.6f})'",
                f"drawbox=x={bar['x']}:y={bar['y']}:w={bar_w}:h={bar['h']}:color={border_hex}:thickness=2:enable='between(t,{start:.6f},{end:.6f})'",
            ]
        )
        epsilon = 1.0 / max(100000.0, steps * fps)
        for i in range(steps):
            t0 = start + i * step
            t1 = start + (i + 1) * step
            fill_w = bar_w * max(0.0, (steps - i - 1) / steps)
            vfilters.append(
                f"drawbox=x={bar['x']}:y={bar['y']}:w={fill_w:.3f}:h={bar['h']}:color={fill_hex}:thickness=fill:enable='between(t,{t0:.4f},{t1 + epsilon:.4f})'"
            )

    timer_part = f"[0:v]{','.join(vfilters)}[timer];"
    question_part = (
        f"[2:v]format=rgba,"
        f"fade=t=in:st=0:d={question_fade_d}:alpha=1[qn];"
    )
    if question_entry_effect == "slideleft":
        qx_expr = f"lte(t\\,{question_slide_d})*({question_slide_d}-t)/{question_slide_d}*(-{question_slide_px})"
        overlay_part = f"[timer][qn]overlay=x={qx_expr}:y=0:format=auto[qtv];"
    else:
        slide_expr = f"lte(t\\,{question_slide_d})*({question_slide_d}-t)/{question_slide_d}*18"
        overlay_part = f"[timer][qn]overlay=x=0:y={slide_expr}:format=auto[qtv];"

    choices_fade_d = float(CONFIG.get("choices_fade_seconds", 0.35))
    choices_lead = float(CONFIG.get("choices_lead_seconds", 0.25))
    choices_start = max(0.0, start - choices_lead)
    choices_entry_effect = str(CONFIG.get("choices_entry_effect", "fade") or "fade").strip().lower()
    choices_slide_d = float(CONFIG.get("choices_slide_seconds", choices_fade_d))
    choices_slide_px = float(CONFIG.get("choices_slide_px", 120))
    choices_part = (
        f"[3:v]format=rgba,"
        f"fade=t=in:st={choices_start}:d={choices_fade_d}:alpha=1[cn];"
    )
    if choices_entry_effect == "slideleft":
        choices_end = choices_start + choices_slide_d
        cx_expr = (
            f"(-{choices_slide_px})*lte(t\\,{choices_start})+"
            f"(-{choices_slide_px}+{choices_slide_px}*(t-{choices_start})/{choices_slide_d})*gte(t\\,{choices_start})*lte(t\\,{choices_end})"
        )
        overlay_part2 = f"[qtv][cn]overlay=x={cx_expr}:y=0:format=auto[vid]"
    else:
        overlay_part2 = f"[qtv][cn]overlay=x=0:y=0:format=auto[vid]"

    v_part = f"{timer_part}{question_part}{overlay_part}{choices_part}{overlay_part2}"

    if clock_mp3 and clock_mp3.exists():
        offset_ms = int(round(start * 1000))
        # Clock'u timer window'u boyunca döndürüp question'ın bittiği ana delay’le.
        a_part = (
            f"[1:a]volume={voice_gain_db}dB,apad=pad_dur={timer_seconds},atrim=0:{total},aformat=channel_layouts=mono,aresample=44100[q];"
            f"[4:a]aformat=channel_layouts=mono,aresample=44100,atrim=start={clock_trim_seconds}:end={clock_window_end}[c];"
            f"[c]adelay={offset_ms}[c2];"
            f"[q][c2]amix=inputs=2:duration=longest:dropout_transition=0[aud]"
        )
        filter_complex = f"{v_part};{a_part}"
    else:
        filter_complex = f"{v_part};[1:a]volume={voice_gain_db}dB,apad=pad_dur={timer_seconds},atrim=0:{total}[aud]"

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(base_png),
        "-i",
        str(audio_wav),
        "-loop",
        "1",
        "-i",
        str(question_layer_png),
        "-loop",
        "1",
        "-i",
        str(choices_layer_png),
    ]

    if clock_mp3 and clock_mp3.exists():
        cmd.extend(["-stream_loop", "-1", "-i", str(clock_mp3)])

    cmd.extend(
        [
            "-t",
            str(total),
            "-r",
            str(fps),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vid]",
            "-map",
            "[aud]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            f"{int(CONFIG.get('audio_bitrate_kbps', 192))}k",
            str(out_mp4),
        ]
    )
    run(cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-tts", action="store_true", help="ElevenLabs olmadan demo audio üretip akışı test etmek için.")
    args = parser.parse_args()

    if not (ROOT / "input" / "questions.json").exists():
        raise RuntimeError("Eksik: `input/questions.json`")

    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not voice_id and not args.mock_tts:
        raise RuntimeError("Set env var: ELEVENLABS_VOICE_ID")
    model_id = os.environ.get("ELEVENLABS_MODEL_ID", CONFIG["elevenlabs"]["model_id_default"])

    questions = json.loads((ROOT / "input" / "questions.json").read_text(encoding="utf-8"))

    audio_cache_dir = ROOT / "audio_cache"
    bases_dir = ROOT / "render" / "bases"
    seg_dir = ROOT / "render" / "segments"
    output_dir = ROOT / "output"
    input_images_dir = ROOT / "input_images"
    clock_mp3 = ROOT / "clock.mp3"
    mock_choice_a = ROOT / "secenek1.jpg"
    mock_choice_b = ROOT / "secenek2.jpg"
    intro_mp4 = ROOT / "VIDEOGIRIS.mp4"
    ad_mp4 = ROOT / "VIDEOARAREKLAM.mp4"
    outro_mp4 = None
    bgm_audio = None
    bgm_volume = 0.0
    transition_sfx = None
    transition_sfx_volume = float(CONFIG.get("transition_sound_volume", 0.35))
    ad_insert_after = [3]  # default: after Q3 (1-based)
    layout_type = "classic_2_choice"
    if RENDER_SETTINGS_JSON.exists():
        try:
            render_settings = json.loads(RENDER_SETTINGS_JSON.read_text(encoding="utf-8"))
            intro_raw = str(render_settings.get("intro_video", "")).strip()
            ad_raw = str(render_settings.get("ad_video", "")).strip()
            outro_raw = str(render_settings.get("outro_video", "")).strip()
            bgm_raw = str(render_settings.get("bg_music", "")).strip()
            transition_sfx_raw = str(render_settings.get("transition_sound", "")).strip()
            bgm_volume = float(render_settings.get("bg_music_volume", 0.25) or 0.0)
            transition_sfx_volume = float(render_settings.get("transition_sound_volume", transition_sfx_volume) or transition_sfx_volume)
            CONFIG["audio_bitrate_kbps"] = int(render_settings.get("audio_bitrate_kbps", CONFIG["audio_bitrate_kbps"]) or CONFIG["audio_bitrate_kbps"])
            CONFIG["custom_audio_gain_db"] = float(render_settings.get("custom_audio_gain_db", CONFIG["custom_audio_gain_db"]) or CONFIG["custom_audio_gain_db"])
            CONFIG["font_family"] = str(render_settings.get("font_family", CONFIG["font_family"]) or CONFIG["font_family"]).strip().lower()
            transition_type_raw = str(render_settings.get("transition_type", CONFIG.get("transition_type", "fade")) or "fade").strip().lower()
            if transition_type_raw in {"fade", "slideleft"}:
                CONFIG["transition_type"] = transition_type_raw
            question_entry_effect_raw = str(render_settings.get("question_entry_effect", CONFIG.get("question_entry_effect", "fade")) or "fade").strip().lower()
            if question_entry_effect_raw in {"fade", "slideleft"}:
                CONFIG["question_entry_effect"] = question_entry_effect_raw
            choices_entry_effect_raw = str(render_settings.get("choices_entry_effect", CONFIG.get("choices_entry_effect", "fade")) or "fade").strip().lower()
            if choices_entry_effect_raw in {"fade", "slideleft"}:
                CONFIG["choices_entry_effect"] = choices_entry_effect_raw
            timer_type_raw = str(render_settings.get("timer_type", CONFIG.get("timer_type", "classic_bar")) or "classic_bar").strip().lower()
            if timer_type_raw in {"classic_bar", "countdown_circle"}:
                CONFIG["timer_type"] = timer_type_raw
            layout_type_raw = str(render_settings.get("layout_type", layout_type) or layout_type).strip().lower()
            if layout_type_raw in {"classic_2_choice", "single_image"}:
                layout_type = layout_type_raw
            ad_positions = render_settings.get("ad_insert_after", ad_insert_after)
            if isinstance(ad_positions, list):
                # sanitize to ints >=1
                ad_insert_after = [int(x) for x in ad_positions if str(x).isdigit() and int(x) >= 1]
            if intro_raw:
                intro_candidate = Path(intro_raw)
                intro_mp4 = intro_candidate if intro_candidate.is_absolute() else (ROOT / intro_candidate).resolve()
            if ad_raw:
                ad_candidate = Path(ad_raw)
                ad_mp4 = ad_candidate if ad_candidate.is_absolute() else (ROOT / ad_candidate).resolve()
            if outro_raw:
                outro_candidate = Path(outro_raw)
                outro_mp4 = outro_candidate if outro_candidate.is_absolute() else (ROOT / outro_candidate).resolve()
            if bgm_raw:
                bgm_candidate = Path(bgm_raw)
                bgm_audio = bgm_candidate if bgm_candidate.is_absolute() else (ROOT / bgm_candidate).resolve()
            if transition_sfx_raw:
                sfx_candidate = Path(transition_sfx_raw)
                transition_sfx = sfx_candidate if sfx_candidate.is_absolute() else (ROOT / sfx_candidate).resolve()
        except Exception:
            # Keep defaults if settings cannot be parsed.
            pass

    audio_cache_dir.mkdir(parents=True, exist_ok=True)
    bases_dir.mkdir(parents=True, exist_ok=True)
    seg_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    seg_paths = []

    # Intro clip at the very beginning (optional)
    if intro_mp4.exists():
        intro_norm = seg_dir / "_intro_norm.mp4"
        normalize_clip_to_standard(intro_mp4, intro_norm)
        seg_paths.append(intro_norm)

    # Prepare normalized ad clip once if present
    ad_norm = None
    if ad_mp4.exists():
        ad_norm = seg_dir / "_ad_norm.mp4"
        normalize_clip_to_standard(ad_mp4, ad_norm)

    # Optional outro clip at the end
    outro_norm = None
    if outro_mp4 and outro_mp4.exists():
        outro_norm = seg_dir / "_outro_norm.mp4"
        normalize_clip_to_standard(outro_mp4, outro_norm)

    # Convert 1-based positions to 0-based indices of "after question idx"
    ad_after_zero_based = set([p - 1 for p in ad_insert_after if isinstance(p, int) and p - 1 >= 0])

    for idx, q in enumerate(questions):
        qid = q.get("id", f"q{idx+1}")
        question = q["question"].strip()
        a_text = ""
        b_text = ""
        a_img = mock_choice_a
        b_img = mock_choice_b
        single_img = mock_choice_a
        if layout_type == "single_image":
            single_img_raw = q.get("image", "")
            if not single_img_raw:
                single_img_raw = q.get("a", {}).get("image", str(input_images_dir / f"{qid}_image.png"))
            single_img = Path(single_img_raw)
            if not single_img.is_absolute():
                single_img = (ROOT / single_img).resolve()
            if not single_img.exists() and mock_choice_a.exists():
                single_img = mock_choice_a
        else:
            a = q.get("a", {})
            b = q.get("b", {})
            a_text = a.get("text", "").strip()
            b_text = b.get("text", "").strip()

            a_img = Path(a.get("image", str(input_images_dir / f"{qid}_a.png")))
            b_img = Path(b.get("image", str(input_images_dir / f"{qid}_b.png")))
            if not a_img.is_absolute():
                a_img = (ROOT / a_img).resolve()
            if not b_img.is_absolute():
                b_img = (ROOT / b_img).resolve()

            # Fallback to repo-level mock images only if question-specific images do not exist.
            if not a_img.exists() and mock_choice_a.exists():
                a_img = mock_choice_a
            if not b_img.exists() and mock_choice_b.exists():
                b_img = mock_choice_b

        voice_text = q.get("voice_text")
        if not voice_text:
            # Senaryoya göre sadece soruyu seslendireceğiz.
            voice_text = question

        # Optional per-question audio file support (mp3/wav/etc).
        # If present, this takes priority over ElevenLabs/mock generation.
        q_audio_raw = q.get("audio")
        q_audio_path = Path(q_audio_raw) if q_audio_raw else None
        if q_audio_path and not q_audio_path.is_absolute():
            q_audio_path = (ROOT / q_audio_path).resolve()

        is_custom_audio = bool(q_audio_path and q_audio_path.exists())
        if q_audio_path and q_audio_path.exists():
            st = q_audio_path.stat()
            key = sha256_str(f"custom_audio|{q_audio_path.name}|{st.st_size}|{int(st.st_mtime)}")
            audio_wav = audio_cache_dir / f"{qid}_{key}.wav"
            if not audio_wav.exists() or audio_wav.stat().st_size == 0:
                convert_any_audio_to_wav(q_audio_path, audio_wav)
        else:
            # For mock mode, allow using ses1.mp3 for all questions.
            mock_mp3 = ROOT / "ses1.mp3" if args.mock_tts and (ROOT / "ses1.mp3").exists() else (ROOT / f"ses{idx+1}.mp3")
            mock_tag = ""
            if args.mock_tts:
                if mock_mp3.exists():
                    st = mock_mp3.stat()
                    mock_tag = f"|mock:{mock_mp3.name}:{st.st_size}:{int(st.st_mtime)}"
                else:
                    mock_tag = "|mock_missing"
            key = sha256_str(voice_text + voice_id + model_id + mock_tag)
            audio_wav = audio_cache_dir / f"{qid}_{key}.wav"
            elevenlabs_tts_to_wav(
                voice_id,
                model_id,
                voice_text,
                audio_wav,
                mock=args.mock_tts,
                mock_mp3=mock_mp3,
            )

        audio_dur = ffprobe_duration_seconds(audio_wav)

        base_png = bases_dir / f"{qid}.png"
        question_layer_png = bases_dir / f"{qid}_question.png"
        choices_layer_png = bases_dir / f"{qid}_choices.png"
        build_base_image(
            base_png,
            question_layer_png,
            choices_layer_png,
            question,
            layout_type,
            a_text,
            b_text,
            a_img,
            b_img,
            single_img,
        )

        out_mp4 = seg_dir / f"{qid}.mp4"
        voice_gain_db_for_question = float(CONFIG["custom_audio_gain_db"]) if is_custom_audio else float(CONFIG["voice_gain_db"])
        make_question_segment(
            base_png,
            question_layer_png,
            choices_layer_png,
            audio_wav,
            out_mp4,
            audio_dur,
            clock_mp3=clock_mp3,
            voice_gain_db=voice_gain_db_for_question,
        )
        seg_paths.append(out_mp4)

        # Insert ad clip after selected questions (dynamic)
        if ad_norm and idx in ad_after_zero_based:
            seg_paths.append(ad_norm)

    if outro_norm:
        seg_paths.append(outro_norm)

    concat_list = seg_dir / "concat_list.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for p in seg_paths:
            # concat demuxer requires forward slashes
            f.write(f"file '{p.resolve().as_posix()}'\n")

    final_out = output_dir / "final_9x16.mp4"
    final_no_bgm = output_dir / "final_9x16_no_bgm.mp4"
    final_with_sfx = output_dir / "final_9x16_with_sfx.mp4"
    transition_seconds = float(CONFIG.get("transition_seconds", 0.0))
    transition_type = str(CONFIG.get("transition_type", "fade"))
    q_to_q_offsets = concat_with_transitions(seg_paths, final_no_bgm, transition_seconds, transition_type)
    add_transition_sfx(
        final_no_bgm,
        transition_sfx if transition_sfx else Path("__missing__"),
        final_with_sfx,
        q_to_q_offsets,
        transition_sfx_volume,
    )
    add_background_music(final_with_sfx, bgm_audio if bgm_audio else Path("__missing__"), final_out, bgm_volume)
    print(f"Wrote: {final_out}")


if __name__ == "__main__":
    main()

