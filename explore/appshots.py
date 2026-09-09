"""Conservatively extract useful UI states from an app walkthrough video."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
import imagehash
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT_ROOT = PROJECT_ROOT / "in"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "out"
DEFAULT_SAMPLE_FPS = 2.0
DEFAULT_STABILITY_THRESHOLD = 0.045  # mean thumbnail change considered stable
DEFAULT_DEDUPE_THRESHOLD = 7  # conservative pHash Hamming distance
IMAGE_PATTERN = re.compile(r"^\d{3,4}\.png$")


def display_path(path: Path) -> str:
    """Keep CLI output portable and free of machine-specific absolute paths."""
    path = path.expanduser()
    for base in (PROJECT_ROOT, Path.cwd().resolve()):
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return path.name


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    codec: str


@dataclass
class Frame:
    path: Path
    index: int
    timestamp: float
    score: float = 0.0
    stable: bool = False
    motion: bool = False
    keep: bool = False
    reason: str = ""
    phash: object | None = None


def tool_check() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError("缺少系统工具：" + ", ".join(missing) + "。请先安装 ffmpeg（其中包含 ffprobe）。")


def run(command: list[str], *, error: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到系统工具 {command[0]}。请先安装 ffmpeg。") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip().splitlines()[-1] if exc.stderr.strip() else "未知错误"
        raise RuntimeError(f"{error}：{detail}") from exc


def resolve_video(value: str, root: Path) -> tuple[str, Path]:
    candidate = Path(value).expanduser()
    if candidate.suffix.lower() in {".mov", ".mp4", ".m4v"} or candidate.exists():
        video = candidate
        app_name = video.parent.name
    else:
        app_name = value
        video = root / value / "walkthrough.mov"
    if not video.exists():
        raise RuntimeError(f"找不到输入视频：{display_path(video)}\n请传 App 名称或 .mov/.mp4/.m4v 文件路径。")
    if not video.is_file():
        raise RuntimeError(f"输入路径不是文件：{display_path(video)}")
    return app_name, video.resolve()


def probe_video(video: Path) -> VideoInfo:
    result = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "format=duration:stream=width,height,avg_frame_rate,codec_name",
                  "-of", "json", str(video)], error="无法读取视频信息")
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        rate_num, rate_den = map(int, stream.get("avg_frame_rate", "0/1").split("/"))
        fps = rate_num / rate_den if rate_den else 0.0
        return VideoInfo(float(data["format"]["duration"]), int(stream["width"]), int(stream["height"]), fps,
                         stream.get("codec_name", "unknown"))
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError) as exc:
        raise RuntimeError("ffprobe 返回的视频信息不完整，无法继续处理。") from exc


def extract_candidate_frames(video: Path, temp_dir: Path, fps: float) -> list[Frame]:
    pattern = temp_dir / "candidate-%06d.png"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", f"fps={fps:g}",
         "-fps_mode", "passthrough", str(pattern)], error="抽取候选帧失败")
    paths = sorted(temp_dir.glob("candidate-*.png"))
    return [Frame(path, i, i / fps) for i, path in enumerate(paths)]


def thumbnail(path: Path) -> Image.Image:
    image = Image.open(path).convert("L")
    width, height = image.size
    # Ignore status bar and home indicator only for analysis; output stays untouched.
    image = image.crop((0, int(height * 0.04), width, int(height * 0.97)))
    return image.resize((96, 192), Image.Resampling.BILINEAR)


def difference(previous: Image.Image, current: Image.Image) -> float:
    # Pillow's built-in point operation keeps this dependency-light and deterministic.
    a = list(previous.getdata())
    b = list(current.getdata())
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) * 255.0)


def analyze(frames: list[Frame], stability_threshold: float) -> int:
    if not frames:
        return 0
    previous = thumbnail(frames[0].path)
    frames[0].stable = True
    stable_count = 1
    for frame in frames[1:]:
        current = thumbnail(frame.path)
        frame.score = difference(previous, current)
        frame.stable = frame.score <= stability_threshold
        previous = current
        if frame.stable:
            stable_count += 1

    # A run of non-stable samples is motion (transition, animation, or scrolling).
    # Keep only the stable state before and after it; isolated strong changes remain
    # eligible, which protects short-lived modals and screens.
    motion_start: int | None = None
    for i, frame in enumerate(frames):
        if frame.score > stability_threshold:
            motion_start = i if motion_start is None else motion_start
        elif motion_start is not None:
            run_length = i - motion_start
            if run_length >= 2:
                for moving in frames[motion_start:i]:
                    moving.motion = True
            motion_start = None
    if motion_start is not None and len(frames) - motion_start >= 2:
        for moving in frames[motion_start:]:
            moving.motion = True

    # Stable intervals: choose the last sample, allowing loading/transition tails to settle.
    i = 0
    while i < len(frames):
        if not frames[i].stable:
            i += 1
            continue
        end = i
        while end + 1 < len(frames) and frames[end + 1].stable:
            end += 1
        frames[end].keep = True
        frames[end].reason = "stable_state"
        i = end + 1

    # Preserve the endpoint around motion. This is intentionally additive: missing a
    # meaningful short page is worse than asking the user to delete one extra PNG.
    for i, frame in enumerate(frames):
        if frame.motion:
            if i > 0:
                frames[i - 1].keep = True
                frames[i - 1].reason = frames[i - 1].reason or "before_motion"
            j = i
            while j + 1 < len(frames) and frames[j + 1].motion:
                j += 1
            if j + 1 < len(frames):
                frames[j + 1].keep = True
                frames[j + 1].reason = frames[j + 1].reason or "after_motion"
    return stable_count


def deduplicate(frames: list[Frame], threshold: int) -> list[Frame]:
    kept: list[Frame] = []
    for frame in frames:
        if not frame.keep:
            continue
        frame.phash = imagehash.phash(Image.open(frame.path).convert("RGB"))
        if any(frame.phash - other.phash <= threshold for other in kept):
            frame.keep = False
            frame.reason = "duplicate"
        else:
            kept.append(frame)
    return kept


def write_output(frames: list[Frame], output_dir: Path) -> None:
    # Only remove files owned by this tool; preserve cover.png, logo.png, etc.
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.iterdir():
        if old.is_file() and IMAGE_PATTERN.fullmatch(old.name):
            old.unlink()
    for number, frame in enumerate(frames, 1):
        destination = output_dir / f"{number:03d}.png"
        shutil.copyfile(frame.path, destination)


def find_screenshots(directory: Path) -> list[Path]:
    """Return all PNGs below a product directory in filename order.

    A product may organize screenshots in one or more child directories.  Keep
    the output ordered by the source filename, and search recursively so those
    products are not silently omitted from the PDF.
    """
    return sorted(
        (path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".png"),
        key=lambda path: (path.name.casefold(), path.as_posix().casefold()),
    )


def display_name(directory: Path) -> str:
    return directory.name[:1].upper() + directory.name[1:]


def draw_page_header(canvas: Canvas, title: str, subtitle: str, y: float) -> None:
    canvas.setFillColor(colors.HexColor("#1f2933"))
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(42, y, title)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(A4[0] - 42, y + 2, subtitle)
    canvas.setStrokeColor(colors.HexColor("#d9dee7"))
    canvas.setLineWidth(0.6)
    canvas.line(42, y - 12, A4[0] - 42, y - 12)


def draw_screenshot_grid(canvas: Canvas, screenshots: list[Path], embedded: dict[Path, Path], start: int, page_title: str, total: int) -> int:
    page_width, page_height = A4
    margin_x, margin_bottom = 42, 38
    header_y = page_height - 48
    draw_page_header(canvas, page_title, f"{total} Screens", header_y)
    grid_top = header_y - 30
    grid_bottom = margin_bottom + 16
    gap_x, gap_y = 18, 18
    cell_width = (page_width - 2 * margin_x - gap_x) / 2
    cell_height = (grid_top - grid_bottom - gap_y) / 2
    index = start
    for row in range(2):
        for col in range(2):
            if index >= len(screenshots):
                break
            cell_x = margin_x + col * (cell_width + gap_x)
            cell_y = grid_top - (row + 1) * cell_height - row * gap_y
            path = screenshots[index]
            label = path.stem
            canvas.setStrokeColor(colors.HexColor("#d9dee7"))
            canvas.setLineWidth(0.7)
            canvas.roundRect(cell_x, cell_y, cell_width, cell_height, 5, stroke=1, fill=0)
            label_y = cell_y + 8
            canvas.setFillColor(colors.HexColor("#667085"))
            canvas.setFont("Helvetica", 8)
            canvas.drawCentredString(cell_x + cell_width / 2, label_y, label)
            try:
                with Image.open(path) as image:
                    image_width, image_height = image.size
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"无法读取截图：{path}") from exc
            max_width = cell_width - 20
            max_height = cell_height - 26
            scale = min(max_width / image_width, max_height / image_height)
            draw_width, draw_height = image_width * scale, image_height * scale
            image_x = cell_x + (cell_width - draw_width) / 2
            image_y = label_y + 14 + (max_height - draw_height) / 2
            canvas.drawImage(str(embedded[path]), image_x, image_y, draw_width, draw_height, preserveAspectRatio=True, mask="auto")
            index += 1
    canvas.setFillColor(colors.HexColor("#98a2b3"))
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(page_width - 42, 18, str(canvas.getPageNumber()))
    return index


def make_pdf(output: Path, groups: list[tuple[Path, list[Path]]], *, overview: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(output), pagesize=A4, pageCompression=1)
    canvas.setTitle("App Research" if overview else display_name(groups[0][0]))
    if overview:
        page_width, page_height = A4
        canvas.setFillColor(colors.HexColor("#1f2933"))
        canvas.setFont("Helvetica-Bold", 26)
        canvas.drawString(42, page_height - 64, "App Research")
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.setFont("Helvetica", 11)
        canvas.drawString(42, page_height - 86, f"{len(groups)} Apps  ·  {sum(len(items) for _, items in groups)} Screens")
        y = page_height - 136
        canvas.setStrokeColor(colors.HexColor("#d9dee7"))
        for directory, screenshots in groups:
            canvas.setFillColor(colors.HexColor("#1f2933"))
            canvas.setFont("Helvetica-Bold", 13)
            canvas.drawString(52, y, display_name(directory))
            canvas.setFillColor(colors.HexColor("#667085"))
            canvas.setFont("Helvetica", 10)
            canvas.drawRightString(page_width - 52, y, f"{len(screenshots)} Screens")
            canvas.line(52, y - 10, page_width - 52, y - 10)
            y -= 34
        canvas.setFillColor(colors.HexColor("#98a2b3"))
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(page_width - 42, 18, str(canvas.getPageNumber()))
        canvas.showPage()
    with tempfile.TemporaryDirectory(prefix="appshots-pdf-") as cache_name:
        cache_dir = Path(cache_name)
        embedded: dict[Path, Path] = {}
        for group_index, (_, screenshots) in enumerate(groups):
            for screenshot_index, path in enumerate(screenshots):
                with Image.open(path) as image:
                    image = image.convert("RGB")
                    image.thumbnail((700, 1500), Image.Resampling.LANCZOS)
                    # Nested screenshot folders commonly reuse names such as
                    # 001.png. Do not let one source overwrite another cache.
                    cached = cache_dir / f"{group_index:04d}-{screenshot_index:04d}-{path.name}"
                    image.save(cached, format="PNG", optimize=True)
                embedded[path] = cached
        for directory, screenshots in groups:
            start = 0
            while start < len(screenshots):
                start = draw_screenshot_grid(canvas, screenshots, embedded, start, display_name(directory), len(screenshots))
                canvas.showPage()
    canvas.save()


def pdf_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="appshots pdf", description="将人工筛选后的 PNG 整理为 PDF")
    parser.add_argument("input", nargs="?", help="App 名称；与 --all 二选一")
    parser.add_argument("--all", action="store_true", help="合并素材根目录下所有有截图的 App")
    parser.add_argument("--root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="截图根目录，默认当前子项目的 out/")
    parser.add_argument("--output", type=Path, help="PDF 输出路径")
    args = parser.parse_args(argv)
    root = args.root.expanduser()
    if args.all and args.input:
        parser.error("--all 不能与 App 名称同时使用")
    if not args.all and not args.input:
        parser.error("请提供 App 名称，或使用 --all")
    if args.all:
        if not root.is_dir():
            raise RuntimeError(f"素材根目录不存在：{display_path(root)}")
        groups = [(directory, find_screenshots(directory)) for directory in sorted(root.iterdir(), key=lambda p: p.name.casefold()) if directory.is_dir()]
        groups = [(directory, screenshots) for directory, screenshots in groups if screenshots]
        if not groups:
            raise RuntimeError(f"未找到任何 PNG：{display_path(root)}")
        output = (args.output or root / "app-research.pdf").expanduser()
    else:
        directory = root / args.input
        if not directory.is_dir():
            raise RuntimeError(f"App not found: {display_path(directory)}")
        screenshots = find_screenshots(directory)
        if not screenshots:
            raise RuntimeError(f"No PNG screenshots found for: {args.input}")
        groups = [(directory, screenshots)]
        output = (args.output or directory / f"{directory.name}.pdf").expanduser()
    total = sum(len(screenshots) for _, screenshots in groups)
    print(f"Apps: {len(groups)}\nScreenshots: {total}")
    for directory, screenshots in groups:
        print(f"{display_name(directory)}: {len(screenshots)}")
    print("\nGenerating PDF...")
    make_pdf(output, groups, overview=args.all)
    print(f"\nOutput:\n{display_path(output)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 iPhone App walkthrough 中提取 UI 页面截图")
    parser.add_argument("input", help="App 名称（位于 --root 下）或视频文件路径")
    parser.add_argument("--root", type=Path, default=DEFAULT_INPUT_ROOT, help="输入素材根目录，默认当前子项目的 in/")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT,
                        help="输出根目录，默认当前子项目的 out/")
    parser.add_argument("--fps", type=float, default=DEFAULT_SAMPLE_FPS, help="候选抽帧频率，默认 2")
    parser.add_argument("--threshold", type=float, default=DEFAULT_DEDUPE_THRESHOLD, help="pHash 去重阈值，默认 7；越大越激进")
    parser.add_argument("--stability-threshold", type=float, default=DEFAULT_STABILITY_THRESHOLD, help=argparse.SUPPRESS)
    parser.add_argument("--keep-temp", action="store_true", help="保留临时候选帧和 analysis.json")
    parser.add_argument("--debug", action="store_true", help="输出逐帧分析信息")
    args = parser.parse_args()
    if args.fps <= 0 or args.threshold < 0 or args.stability_threshold < 0:
        parser.error("--fps 必须大于 0，阈值不能为负数")
    return args


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "pdf":
        try:
            return pdf_main(sys.argv[2:])
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    args = parse_args()
    try:
        tool_check()
        app_name, video = resolve_video(args.input, args.root)
        info = probe_video(video)
        print(f"App: {app_name}\nVideo: {display_path(video)}\nDuration: {info.duration:.1f}s\nResolution: {info.width}x{info.height}\nSource FPS: {info.fps:g}\n")
        temp_context = tempfile.TemporaryDirectory(prefix="appshots-")
        temp_dir = Path(temp_context.name)
        try:
            print("Extracting...")
            frames = extract_candidate_frames(video, temp_dir, args.fps)
            print(f"Candidate frames: {len(frames)}\nAnalyzing stability...")
            stable_count = analyze(frames, args.stability_threshold)
            print(f"Stable candidates: {stable_count}\nDeduplicating...")
            final = deduplicate(frames, args.threshold)
            output_dir = args.output_root.expanduser() / app_name
            write_output(final, output_dir)
            if args.keep_temp:
                debug = [{"frame": f.index, "timestamp": round(f.timestamp, 3), "motion_score": round(f.score, 5),
                          "stable": f.stable, "motion": f.motion, "kept": f.keep, "reason": f.reason} for f in frames]
                (output_dir / "analysis.json").write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Temp/debug: {temp_dir} (analysis.json written)")
            if args.debug:
                for f in frames:
                    print(f"{f.index:04d} {f.timestamp:6.2f}s diff={f.score:.3f} stable={f.stable} motion={f.motion} keep={f.keep} {f.reason}")
            print(f"Final screenshots: {len(final)}\nOutput: {display_path(output_dir)}/")
        finally:
            if args.keep_temp:
                kept = temp_dir.with_name(temp_dir.name + "-kept")
                shutil.copytree(temp_dir, kept)
                print(f"Candidate frames kept at: {kept}")
            temp_context.cleanup()
        return 0
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
