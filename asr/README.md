# asr

按文件复制顺序，将 `in/` 中的音频文件用 whisper.cpp 转换为文字。默认提示词针对“中文为主、夹杂英文的高尔夫内容”，会尽量保留高尔夫术语、球杆名、品牌和英文缩写。

## 系统依赖

需要全局安装 Homebrew 的 whisper.cpp，以及用于兼容任意常见音频格式的 ffmpeg：

```bash
brew install whisper-cpp ffmpeg
```

Homebrew 安装的 whisper.cpp 命令名是 `whisper-cli`。

## 模型和环境变量

模型默认使用 `ggml-large-v3-turbo.bin`，存放在本子项目的 `models/` 目录。首次下载：

```bash
cd /Users/allovince/Developer/routine/asr
uv run asr download-model
```

默认配置可以直接使用；需要调整时，复制模板为本地 `.env`（`.env` 不提交）：

```bash
cp .env.example .env
```

可配置的环境变量：

- `ASR_MODEL_PATH`：模型文件路径。
- `ASR_MODEL_URL`：`download-model` 使用的模型地址。
- `ASR_PROMPT`：传给 whisper.cpp 的初始提示词。
- `ASR_LANGUAGE`：默认 `auto`，也可指定 `zh` 或其他语言代码。
- `ASR_THREADS`：默认 `8`。
- `ASR_WHISPER_BIN`：默认 `whisper-cli`，用于指定可执行文件路径。

## 使用

把一个或多个音频文件复制到 `in/` 后运行。macOS 上脚本优先按文件创建时间（复制时间）排序；其他系统使用修改时间作为顺序依据；如果时间完全相同，再按文件名稳定排序。建议一次复制完成后不要再手动改动输入文件。

```bash
cd /Users/allovince/Developer/routine/asr
uv sync
uv run asr download-model
uv run asr
```

每个音频生成一个带序号的 `.txt`，同时生成按相同顺序合并的 `out/transcript.txt`。输入和输出目录中的素材不纳入版本控制。
