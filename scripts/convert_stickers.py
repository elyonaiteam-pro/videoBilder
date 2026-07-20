"""
Конвертация Telegram-стикеров (.tgs, gzip Lottie JSON) в анимированный WebP
с альфа-каналом, без внешних зависимостей (Cairo/ffmpeg/rlottie) -
только чистый Python (lottie + resvg_py + Pillow).

Результат: new_main_assets/stickers_webp/<pack>/<n>.webp

Почему WebP, а не APNG: тот же контент в APNG весит в 8-15 раз больше
(проверено на первых пакетах) - критично и для размера git-репозитория,
и для RAM/диска на Render free tier. WebP с lossy-сжатием даёт
приемлемое качество для маленького стикера-оверлея поверх видео.

Идемпотентно: уже сконвертированные файлы пропускаются, скрипт можно
запускать многократно, пока не обработает все паки (у run_command есть
таймаут по времени на один запуск).
"""
import io
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

from lottie.parsers.tgs import parse_tgs
from lottie.exporters.svg import export_svg
import resvg_py
from PIL import Image

ROOT = Path(r"E:\Projects\videobilder-main")
SRC_DIR = ROOT / "new_main_assets" / "mainstikers"
OUT_DIR = ROOT / "new_main_assets" / "stickers_webp"

OUT_FPS = 15
OUT_SIZE = 256
MAX_DURATION_S = 2.5
WEBP_QUALITY = 70
TIME_BUDGET_S = 260  # держим запас под таймаут run_command (300с)


def render_one(tgs_path_str: str, out_path_str: str) -> tuple[str, str]:
    tgs_path = Path(tgs_path_str)
    out_path = Path(out_path_str)
    try:
        anim = parse_tgs(str(tgs_path))
        src_fps = float(anim.frame_rate) or 60.0
        n_frames_total = int(anim.out_point - anim.in_point)
        duration_s = min(n_frames_total / src_fps, MAX_DURATION_S)
        out_n_frames = max(1, int(duration_s * OUT_FPS))
        frame_duration_ms = int(1000 / OUT_FPS)

        frames = []
        for i in range(out_n_frames):
            t = i / OUT_FPS
            src_frame = anim.in_point + t * src_fps
            if src_frame >= anim.out_point:
                break
            buf = io.StringIO()
            export_svg(anim, buf, frame=src_frame)
            png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=buf.getvalue()))
            img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
            if img.width != OUT_SIZE:
                img = img.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
            frames.append(img)

        if not frames:
            return (tgs_path_str, "EMPTY")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            out_path,
            save_all=True,
            append_images=frames[1:],
            format="WEBP",
            duration=frame_duration_ms,
            loop=0,
            quality=WEBP_QUALITY,
            method=6,
        )
        return (tgs_path_str, "OK")
    except Exception as exc:  # noqa: BLE001
        return (tgs_path_str, f"ERROR: {exc}")


def main() -> None:
    jobs = []
    for pack_dir in sorted(SRC_DIR.iterdir()):
        if not pack_dir.is_dir():
            continue
        out_pack_dir = OUT_DIR / pack_dir.name
        for tgs_file in sorted(pack_dir.glob("*.tgs")):
            out_file = out_pack_dir / (tgs_file.stem + ".webp")
            if out_file.exists() and out_file.stat().st_size > 0:
                continue
            jobs.append((str(tgs_file), str(out_file)))

    print(f"TOTAL_PENDING={len(jobs)}")
    if not jobs:
        print("DONE_ALL")
        return

    start = time.time()
    done = 0
    errors = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(render_one, a, b): a for a, b in jobs}
        for fut in as_completed(futures):
            src, status = fut.result()
            done += 1
            if status != "OK":
                errors.append((src, status))
            if time.time() - start > TIME_BUDGET_S:
                print(f"TIME_BUDGET_REACHED processed={done}/{len(jobs)}")
                break

    print(f"PROCESSED={done}/{len(jobs)}")
    if errors:
        print(f"ERRORS={len(errors)}")
        for src, status in errors[:10]:
            print(f"  {src}: {status}")


if __name__ == "__main__":
    main()
