"""Transcribe audio files in copy order with whisper.cpp."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "in"
DEFAULT_OUTPUT = PROJECT_ROOT / "out"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "ggml-large-v3-turbo.bin"
DEFAULT_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
DEFAULT_PROMPT = (
    "这是一段关于高尔夫的中文录音，可能夹杂英文。请准确识别高尔夫术语、球杆名称、球员姓名、"
    "品牌和英文缩写；保留原意，不要翻译，不要补写没有说出的内容。"
)
SUPPORTED_DIRECT = {".flac", ".mp3", ".ogg", ".wav"}
SAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")
GENERATED_TEXT = re.compile(r"^\d{3}-.*\.txt$")


def load_dotenv() -> None:
    """Load the local, ignored .env without adding a dotenv dependency."""
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip("'\"")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"找不到 {name}。请先安装对应系统依赖。")
    return path


def run(command: list[str], *, error: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout).strip().splitlines()
        raise RuntimeError(f"{error}：{detail[-1] if detail else '未知错误'}") from exc


def audio_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise RuntimeError(f"输入目录不存在：{input_dir}")
    files = [path for path in input_dir.iterdir() if path.is_file() and not path.name.startswith(".")]
    # Finder copies have a new creation time while usually preserving the
    # source's modification time. Prefer creation time to reflect copy order.
    return sorted(
        files,
        key=lambda path: (
            getattr(path.stat(), "st_birthtime_ns", int(path.stat().st_birthtime * 1_000_000_000))
            if hasattr(path.stat(), "st_birthtime")
            else path.stat().st_mtime_ns,
            path.name.casefold(),
            path.name,
        ),
    )


def safe_stem(path: Path) -> str:
    stem = SAFE_NAME.sub("_", path.stem).strip("._") or "audio"
    return stem[:80]


def convert_to_wav(source: Path, destination: Path) -> None:
    run(
        [
            require_tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(destination),
        ],
        error=f"转换音频失败：{source.name}",
    )


def transcribe_one(whisper: str, model: Path, prompt: str, source: Path, output: Path, threads: str) -> None:
    with tempfile.TemporaryDirectory(prefix="routine-asr-") as temp:
        wav = Path(temp) / "audio.wav"
        convert_to_wav(source, wav)
        result = run(
            [
                whisper,
                "--model",
                str(model),
                "--language",
                env("ASR_LANGUAGE", "auto"),
                "--threads",
                threads,
                "--prompt",
                prompt,
                "--no-timestamps",
                "--no-prints",
                str(wav),
            ],
            error=f"转写失败：{source.name}",
        )
        text = result.stdout.strip()
        output.write_text(text + ("\n" if text else ""), encoding="utf-8")


def download_model(model: Path, url: str) -> None:
    model.parent.mkdir(parents=True, exist_ok=True)
    if model.exists():
        print(f"模型已存在：{model}")
        return
    print(f"下载模型到 {model} …")
    try:
        urllib.request.urlretrieve(url, model)
    except Exception:
        model.unlink(missing_ok=True)
        raise RuntimeError("模型下载失败，请检查网络后重试。")


def transcribe(input_dir: Path, output_dir: Path) -> int:
    whisper = env("ASR_WHISPER_BIN", "whisper-cli")
    if not Path(whisper).exists():
        require_tool(whisper)
    model = Path(env("ASR_MODEL_PATH", str(DEFAULT_MODEL))).expanduser()
    if not model.is_absolute():
        model = PROJECT_ROOT / model
    if not model.is_file():
        raise RuntimeError(f"找不到模型：{model}\n先运行：uv run asr download-model")
    files = audio_files(input_dir)
    if not files:
        print(f"输入目录为空：{input_dir}")
        return 0
    prompt = env("ASR_PROMPT", DEFAULT_PROMPT)
    threads = env("ASR_THREADS", "8")
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.iterdir():
        if old.is_file() and GENERATED_TEXT.fullmatch(old.name):
            old.unlink()
    (output_dir / "transcript.txt").unlink(missing_ok=True)
    combined: list[str] = []
    for index, source in enumerate(files, 1):
        target = output_dir / f"{index:03d}-{safe_stem(source)}.txt"
        print(f"[{index}/{len(files)}] {source.name} -> {target.name}")
        transcribe_one(whisper, model, prompt, source, target, threads)
        combined.append(f"## {index:03d} {source.name}\n\n{target.read_text(encoding='utf-8').strip()}\n")
    (output_dir / "transcript.txt").write_text("\n".join(combined), encoding="utf-8")
    return len(files)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="按文件名顺序将 in/ 中的音频转成文字")
    parser.add_argument("command", nargs="?", choices=("transcribe", "download-model"), default="transcribe")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-url", default=env("ASR_MODEL_URL", DEFAULT_MODEL_URL))
    args = parser.parse_args()
    try:
        if args.command == "download-model":
            model = Path(env("ASR_MODEL_PATH", str(DEFAULT_MODEL))).expanduser()
            if not model.is_absolute():
                model = PROJECT_ROOT / model
            download_model(model, args.model_url)
            return 0
        count = transcribe(args.input.expanduser(), args.output.expanduser())
        print(f"完成：{count} 个音频，结果写入 {args.output}")
        return 0
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
