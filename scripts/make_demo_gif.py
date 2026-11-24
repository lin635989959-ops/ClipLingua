"""使用本机 Edge/Chrome Headless 截取真实界面并合成 docs/assets/demo.gif。

全程使用临时隔离目录与 100% 原创无版权 Demo 课程，不污染用户数据。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "docs" / "assets"
TMP_LESSONS = Path(tempfile.mkdtemp(prefix="cliplingua_gif_"))
os.environ["CLIPLINGUA_LESSONS_DIR"] = str(TMP_LESSONS)

sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

import create_demo  # noqa: E402
import server  # noqa: E402


def _find_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main() -> None:
    browser = _find_browser()
    if not browser:
        sys.exit("No Edge/Chrome browser found for headless screenshots.")

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    create_demo.create_demos()

    port = 5179
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    th = threading.Thread(
        target=lambda: server.app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False),
        daemon=True,
    )
    th.start()

    # 等待服务就绪
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)

    shots = [
        ("01_home.png", f"http://127.0.0.1:{port}/", "1. Multilingual Dashboard (SV / EN / ES / ZH)"),
        ("02_reader.png", f"http://127.0.0.1:{port}/lesson/demo_sv_coffee", "2. Word-Level Timestamps + OCR & AI Verification"),
        ("03_translate.png", f"http://127.0.0.1:{port}/lesson/demo_sv_coffee/translate", "3. Zero-API-Key Bilingual Translation & Lemma Extraction"),
        ("04_vocab.png", f"http://127.0.0.1:{port}/vocab", "4. Cross-Lesson Vocabulary Book with Lemma Merging"),
        ("05_review.png", f"http://127.0.0.1:{port}/review", "5. Guilt-Free Spaced Repetition (4 Progressive Modes)"),
    ]

    tmp_shots = Path(tempfile.mkdtemp(prefix="cliplingua_shots_"))
    captured: list[tuple[Path, str]] = []
    try:
        for fname, url, caption in shots:
            out_png = tmp_shots / fname
            subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--window-size=1200,760",
                    "--virtual-time-budget=2500",
                    f"--screenshot={out_png}",
                    url,
                ],
                check=True,
                capture_output=True,
            )
            captured.append((out_png, caption))
            print(f"  Captured {fname}")

        from PIL import Image, ImageDraw, ImageFont

        W, H = 960, 648  # 608px 内容 + 40px 顶部标题条
        banner_h = 40
        try:
            font = ImageFont.truetype("segoeui.ttf", 18)
        except OSError:
            font = ImageFont.load_default()

        slides = []
        for idx, (p, caption) in enumerate(captured):
            raw = Image.open(p).convert("RGB").resize((W, H - banner_h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (W, H), (15, 23, 42))
            draw = ImageDraw.Draw(canvas)
            draw.text((18, 10), f"ClipLingua  |  {caption}", fill=(241, 245, 249), font=font)
            canvas.paste(raw, (0, banner_h))
            slides.append(canvas)
            if idx == 1:
                # 同时保存一张清晰静态预览图供文档展示
                canvas.save(ASSETS_DIR / "preview.png", optimize=True)

        # 生成带平滑交叉淡化过渡的动图帧序列
        frames = []
        durations = []
        for i, cur in enumerate(slides):
            nxt = slides[(i + 1) % len(slides)]
            frames.append(cur.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
            durations.append(2400)
            for step in (0.33, 0.67):
                blended = Image.blend(cur, nxt, step)
                frames.append(blended.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
                durations.append(90)

        gif_path = ASSETS_DIR / "demo.gif"
        frames[0].save(
            gif_path,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=True,
        )
        print(f"Generated {gif_path} ({gif_path.stat().st_size / 1024:.1f} KB)")
    finally:
        shutil.rmtree(tmp_shots, ignore_errors=True)
        shutil.rmtree(TMP_LESSONS, ignore_errors=True)


if __name__ == "__main__":
    main()
