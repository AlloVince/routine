# psv

把 `psv/in/` 中的 Adrenaline VPK、PS1 `7z` 镜像转换并通过 VitaShell FTP 上传到 PSV。

## 准备

本机需要 `7z`、Python 3.11+ 和 [pop-fe](https://github.com/sahlberg/pop-fe)。`pop-fe.py` 必须在 `PATH` 中（或设置 `POP_FE`）。pop-fe 负责把 CUE/BIN、IMG 等 PS1 镜像生成 Adrenaline 可读取的 `EBOOT.PBP`；脚本不会伪造或猜测光盘元数据。

将文件放入 `psv/in/`：

- `Adrenaline.vpk`（或任意 `.vpk`，默认自动识别）
- PS1 镜像 `.7z`
- 可选：`661.PBP`。这是 Adrenaline 首次启动需要的官方 PSP 6.61 固件文件；也可用 `PSV_661_PBP_URL` 指向你已确认的官方来源，脚本会下载后上传为 `ux0:/app/PSPEMUCFW/661.PBP`。

## 运行

```bash
cd psv
uv run psv
```

默认 FTP 为 `ftp://192.168.50.6:1337`。也可以显式设置：

```bash
PSV_FTP_URL=ftp://192.168.50.6:1337 uv run psv
```

默认直连 PSV，不继承系统代理，避免局域网传输绕行。确需 SOCKS5 代理时使用 `PSV_USE_PROXY=1 uv run psv`，代理地址依次读取 `PSV_SOCKS_PROXY`、`all_proxy`、`ALL_PROXY`。

每次运行都会检查并上传 VPK 到 `ux0:/data`，把 PS1 游戏放到 `ux0:/pspemu/PSP/GAME/<游戏名>/EBOOT.PBP`，同名的 `Disc 1`、`Disc 2` 等 7z 会合并为一个多碟 EBOOT，并用 `.state.json` 记录本地文件 SHA-256，后续新增 ROM 只处理新增或已变化的文件。转换结果保存在 `psv/out/`，不提交到 Git。

首次使用仍需在 PSV 的 VitaShell 中安装 VPK；Adrenaline 本身的首次初始化可能要求启动一次并下载/安装 6.61 固件。FTP 只能传输文件，不能替代 PSV 上的安装和首次启动确认。
