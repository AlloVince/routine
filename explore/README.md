# explore

从 iPhone App walkthrough 录屏中提取 UI 页面截图，并整理为 PDF。

## 安装

```bash
cd explore
uv sync
```

系统需要可执行的 `ffmpeg` 和 `ffprobe`。

## 使用

默认读取 `in/<app>/walkthrough.mov`，并将编号 PNG 写入 `out/<app>/`：

```bash
uv run appshots trackman
uv run appshots trackman
```

调参：`--fps 2` 控制候选抽帧密度；`--threshold 7` 控制保守 pHash 去重（越大越激进）；`--keep-temp` 保留候选帧和 `analysis.json`；`--root` 修改素材根目录。

## PDF

人工删除不需要的 PNG 后：

```bash
uv run appshots pdf trackman
uv run appshots pdf --all
```

单产品 PDF 和全量 PDF 都写入 `out/`。PDF 会递归读取每个 App 目录及其子目录中的 PNG，并按文件名顺序排列。
