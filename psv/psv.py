from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from ftplib import FTP
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
INPUT = ROOT / "in"
OUTPUT = ROOT / "out"
STATE = OUTPUT / ".state.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name[:64] or "GAME"


def ftp_path(ftp: FTP, path: str) -> None:
    parts = [part for part in path.strip("/").split("/") if part]
    ftp.cwd("/")
    for part in parts:
        try:
            ftp.cwd(part)
        except Exception:
            ftp.mkd(part)
            ftp.cwd(part)


def upload(ftp: FTP, local: Path, remote: str) -> None:
    parent, filename = remote.rsplit("/", 1)
    ftp_path(ftp, parent)
    try:
        ftp.delete(filename)
    except Exception:
        pass
    # VitaShell's FTP server does not reliably implement RNFR/RNTO, so use
    # STOR on the final name. A failed transfer can be safely retried.
    try:
        ftp.delete(f"{filename}.part")
    except Exception:
        pass
    total = local.stat().st_size
    sent = 0
    started = time.monotonic()
    last_report = -1

    def report(block: bytes) -> None:
        nonlocal sent, last_report
        sent += len(block)
        percent = int(sent * 100 / total) if total else 100
        now = time.monotonic()
        if percent != last_report and (percent % 5 == 0 or percent == 100):
            elapsed = max(now - started, 0.001)
            speed = sent / elapsed
            remaining = (total - sent) / speed if speed else 0
            print(
                f"上传 {remote}: {percent:3d}% "
                f"({sent / 1024**2:.1f}/{total / 1024**2:.1f} MiB), "
                f"{speed / 1024**2:.2f} MiB/s, 剩余约 {remaining / 60:.1f} 分钟",
                flush=True,
            )
            last_report = percent

    with local.open("rb") as stream:
        ftp.storbinary(f"STOR {filename}", stream, callback=report)
    print(f"上传完成: {remote}", flush=True)


def download_firmware() -> Path | None:
    local = next((p for p in INPUT.glob("661.PBP") if p.is_file()), None)
    if local:
        return local
    url = os.getenv("PSV_661_PBP_URL")
    if not url:
        return None
    target = OUTPUT / "661.PBP"
    print(f"下载 6.61 固件: {url}")
    urllib.request.urlretrieve(url, target)
    return target


def game_name(archive: Path) -> str:
    return safe_name(re.sub(r"\s*\(?(?:disc|disk)\s*\d+\)?$", "", archive.stem, flags=re.I).strip())


def convert(archives: list[Path]) -> Path:
    game = game_name(archives[0])
    target = OUTPUT / game / "EBOOT.PBP"
    if target.exists():
        return target
    pop_fe = os.getenv("POP_FE", "pop-fe.py")
    with tempfile.TemporaryDirectory(prefix="psv-7z-") as temp:
        extracted = Path(temp) / "disc"
        extracted.mkdir()
        extractor = shutil.which("7z") or shutil.which("7zz")
        if not extractor:
            raise RuntimeError("缺少 7z/7zz，请先安装 p7zip 或 7zip")
        for archive in archives:
            subprocess.run([extractor, "x", "-y", f"-o{extracted / archive.stem}", str(archive)], check=True)
        candidates = sorted(
            p for p in extracted.rglob("*")
            if p.suffix.lower() in {".cue", ".ccd", ".img", ".iso", ".chd"}
        )
        if not candidates:
            raise RuntimeError(f"{archives[0].name} 中没有找到 CUE/CCD/IMG/ISO/CHD")
        target.parent.mkdir(parents=True, exist_ok=True)
        pop_fe_path = Path(pop_fe).resolve()
        subprocess.run(
            [str(pop_fe_path), f"--psp-dir={target.parent.parent}", *(str(path) for path in candidates)],
            check=True,
            cwd=pop_fe_path.parent,
        )
        generated = next(target.parent.parent.glob("*/EBOOT.PBP"), None)
        if generated is None:
            raise RuntimeError(f"pop-fe 未生成 EBOOT.PBP: {archives[0].name}")
        if generated != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(generated, target)
    return target


def main() -> None:
    INPUT.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    ftp_url = os.getenv("PSV_FTP_URL", "ftp://192.168.50.6:1337")
    parsed = urlparse(ftp_url)
    if parsed.scheme != "ftp" or not parsed.hostname:
        raise SystemExit("PSV_FTP_URL 必须是 ftp://host:port")
    use_proxy = os.getenv("PSV_USE_PROXY", "0") != "0"
    proxy_url = None
    if use_proxy:
        proxy_url = os.getenv("PSV_SOCKS_PROXY") or os.getenv("all_proxy") or os.getenv("ALL_PROXY")
    if proxy_url:
        import socks

        proxy = urlparse(proxy_url)
        if proxy.scheme not in {"socks5", "socks5h"} or not proxy.hostname:
            raise SystemExit("PSV_SOCKS_PROXY 必须是 socks5://host:port")
        socks.set_default_proxy(
            socks.SOCKS5,
            proxy.hostname,
            proxy.port or 1080,
            rdns=proxy.scheme == "socks5h",
            username=proxy.username,
            password=proxy.password,
        )
        socket.socket = socks.socksocket
    firmware = download_firmware()
    vpk = next(iter(sorted(INPUT.glob("*.vpk"))), None)
    archives = sorted(INPUT.glob("*.7z"))
    if not vpk and not firmware and not archives:
        raise SystemExit("psv/in/ 中没有 VPK、661.PBP 或 7z 文件")
    with FTP() as ftp:
        last_error = None
        for attempt in range(1, 4):
            try:
                ftp.connect(parsed.hostname, parsed.port or 21, timeout=15)
                break
            except OSError as error:
                last_error = error
                if attempt == 3:
                    raise RuntimeError(f"无法连接 PSV FTP {parsed.hostname}:{parsed.port or 21}: {error}") from error
        if last_error and ftp.sock is None:
            raise last_error
        ftp.login(os.getenv("PSV_FTP_USER", "anonymous"), os.getenv("PSV_FTP_PASSWORD", ""))
        if vpk:
            key = f"vpk:{vpk.name}"
            if state.get(key) != sha256(vpk):
                upload(ftp, vpk, "ux0:/data/" + vpk.name)
                state[key] = sha256(vpk)
        if firmware:
            key = "firmware:661.PBP"
            if state.get(key) != sha256(firmware):
                upload(ftp, firmware, "ux0:/app/PSPEMUCFW/661.PBP")
                state[key] = sha256(firmware)
        groups: dict[str, list[Path]] = {}
        for archive in archives:
            groups.setdefault(game_name(archive), []).append(archive)
        ordered_groups = sorted(groups.items(), key=lambda item: ("Houshin" not in item[0], item[0]))
        for game, group in ordered_groups:
            key = "rom:" + ",".join(path.name for path in group)
            digest = hashlib.sha256("".join(sha256(path) for path in group).encode()).hexdigest()
            if state.get(key) == digest:
                continue
            eboot = convert(group)
            upload(ftp, eboot, f"ux0:/pspemu/PSP/GAME/{game}/EBOOT.PBP")
            state[key] = digest
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
