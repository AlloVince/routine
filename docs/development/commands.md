# 开发与运行命令

## 何时读
安装依赖、运行 `explore` 或生成 PDF 时。

## 内容
当前已确认命令针对 `explore` 子项目：

```bash
cd /Users/allovince/Developer/routine/explore
uv sync
uv run appshots <app>
uv run appshots pdf <app>
uv run appshots pdf --all
```

默认输入为 `explore/in/<app>/walkthrough.mov`，编号 PNG 写入 `explore/out/<app>/`。生成 PDF 前可人工删除不需要的 PNG。系统需要 `ffmpeg` 和 `ffprobe`。

`--fps 2` 控制候选抽帧密度，`--threshold 7` 控制 pHash 去重，`--keep-temp` 保留临时分析文件，`--root` 修改素材根目录。仓库目前未记录独立测试命令；待补充实际测试入口。

## `asr`

系统需要 Homebrew 的 `whisper-cpp` 和 `ffmpeg`。模型下载到 `asr/models/`，音频放入 `asr/in/`，结果写入 `asr/out/`：

```bash
cd /Users/allovince/Developer/routine/asr
uv sync
uv run asr download-model
uv run asr
```

脚本按输入文件的创建时间（macOS 的复制时间；其他系统使用修改时间）顺序处理，并生成逐文件 TXT 与合并的 `out/transcript.txt`。模型、提示词、语言、线程数和可执行文件可通过 `.env` 中的 `ASR_*` 环境变量控制；`.env`、模型和素材不提交。

## 相关
- 代码：`explore/appshots.py`、`explore/pyproject.toml`
- 用法：`explore/README.md`
