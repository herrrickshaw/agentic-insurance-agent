"""Render the demo video from real captured run data (video/capture.json): frames (PIL) + macOS `say` + ffmpeg."""
import json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_models as bm

OUT = ROOT / "video" / "out"; OUT.mkdir(exist_ok=True)
W, H = 1920, 1080
BG, FG, MUT, ACC, OK, WARN, BAD, CARD = "#0f172a", "#e2e8f0", "#94a3b8", "#38bdf8", "#34d399", "#fbbf24", "#f87171", "#1e293b"
F = lambda s, b=False: ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", s, index=1 if b else 0)
M = lambda s: ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", s)


def canvas(title, sub=None):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    d.text((80, 50), title, font=F(54, True), fill=FG)
    if sub: d.text((80, 125), sub, font=F(30), fill=MUT)
    d.rectangle((0, H - 8, W, H), fill=ACC)
    return im, d


def wrap(d, text, font, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > width: lines.append(cur); cur = w
        else: cur = t
    return lines + [cur]


def bullets(d, items, x, y, size=36, width=1700, color=FG):
    for it in items:
        d.ellipse((x, y + size // 2 - 6, x + 12, y + size // 2 + 6), fill=ACC)
        for ln in wrap(d, it, F(size), width):
            d.text((x + 36, y), ln, font=F(size), fill=color); y += size + 12
        y += 14
    return y


def bpmn(proc, hi=(), title="", sub=""):
    """Draw the diagram 1:1 on its own image; wide diagrams are panned across the screen in ffmpeg."""
    bg, _ = canvas(title, sub)
    xs = [proc.box(n) for n in proc.nodes]
    maxx = max(b[0] + b[2] for b in xs) + 80; maxy = max(b[1] + b[3] for b in xs) + 80
    im = Image.new("RGB", (maxx, maxy), BG); d = ImageDraw.Draw(im)
    for f in proc.flows:
        pts = list(proc.waypoints(f["src"], f["tgt"]))
        d.line(pts, fill=MUT, width=3)
        ex, ey = pts[-1]; d.polygon([(ex, ey), (ex - 12, ey - 6), (ex - 12, ey + 6)], fill=MUT)
        if f["label"]:
            mx, my = pts[1] if len(pts) > 2 else pts[0]
            d.text((mx + 6, my - 26), f["label"], font=F(20), fill=WARN)
    for nid, n in proc.nodes.items():
        x0, y0, w, h = proc.box(nid); x1, y1 = x0 + w, y0 + h
        col = ACC if nid in hi else MUT
        k = n["kind"]
        if k in ("start", "end", "msg", "timer"):
            d.ellipse((x0, y0, x1, y1), outline=col if k != "end" else BAD, width=5 if k == "end" else 3, fill=CARD)
            d.text(((x0 + x1) / 2, y1 + 6), n["name"], font=F(20), fill=FG, anchor="ma")
        elif k in ("xor", "evgw"):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            d.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], outline=col, fill=CARD, width=3)
            d.text((cx, y1 + 4), n["name"], font=F(20), fill=FG, anchor="ma")
        else:
            fill = {"user": "#3b2f0b", "dmn": "#14322a"}.get(k, CARD)
            ai = n["a"].get("topic", "").startswith("ai-")
            d.rounded_rectangle((x0, y0, x1, y1), 12, outline=OK if ai else col, fill=fill, width=5 if nid in hi or ai else 3)
            for i, ln in enumerate(wrap(d, n["name"], F(19), w - 12)[:4]):
                d.text(((x0 + x1) / 2, y0 + 8 + i * 22), ln, font=F(19), fill=FG, anchor="ma")
    if maxx <= W - 100:
        bg.paste(im, ((W - maxx) // 2, 300)); return bg, None
    return bg, im


def table(d, x, y, cols, rows, widths, size=26):
    d.rectangle((x, y, x + sum(widths), y + 52), fill=CARD)
    cx = x
    for c, w in zip(cols, widths): d.text((cx + 12, y + 10), c, font=F(size, True), fill=ACC); cx += w
    y += 56
    for r in rows:
        cx = x
        for v, w in zip(r, widths):
            col = OK if str(v) in ("paid", "active") else WARN if str(v) in ("pending", "invoiced", "open") else FG
            d.text((cx + 12, y), str(v)[:34], font=F(size), fill=col); cx += w
        y += 46
    return y


def log(d, lines, x, y, size=24, maxlines=14):
    for ref, ev in lines[-maxlines:]:
        d.text((x, y), ref, font=M(size), fill=ACC); d.text((x + 380, y), ev[:95], font=M(size), fill=FG); y += size + 14




def assemble(scenes, name):
    seg = []
    for i, (im, narr, wide) in enumerate(scenes):
        png, aiff = OUT / f"s{i:02d}.png", OUT / f"s{i:02d}.aiff"
        im.save(png)
        subprocess.run(["say", "-v", "Samantha", "-r", "170", "-o", str(aiff), narr], check=True)
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(aiff)]).decode()) + 0.8
        mp4 = OUT / f"s{i:02d}.mp4"
        fade = f"fade=t=in:d=0.4,fade=t=out:st={dur-0.4:.2f}:d=0.4,format=yuv420p"
        if wide is None:
            cmd = ["-loop", "1", "-i", str(png), "-i", str(aiff), "-t", f"{dur:.2f}", "-vf", fade]
        else:
            dpng = OUT / f"s{i:02d}_diagram.png"; wide.save(dpng)
            flt = (f"[0][1]overlay=x='if(gt(overlay_w,main_w),-(overlay_w-main_w)*min(t/{dur-1.5:.2f},1),(main_w-overlay_w)/2)':y=260,{fade}")
            cmd = ["-loop", "1", "-i", str(png), "-loop", "1", "-i", str(dpng), "-i", str(aiff), "-t", f"{dur:.2f}", "-filter_complex", flt]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd, "-r", "30", "-c:v", "libx264", "-c:a", "aac", "-af", "apad",
                        "-shortest", str(mp4)], check=True)
        seg.append(mp4)
    (OUT / "list.txt").write_text("".join(f"file '{s.name}'\n" for s in seg))
    final = ROOT / "video" / name
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(OUT / "list.txt"), "-c", "copy", str(final)], check=True)
    print(final, final.stat().st_size // 1024, "KB")
    return final
