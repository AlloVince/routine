# 项目概览

## 何时读
需要理解仓库组成、子项目边界或输入输出约定时。

## 内容
`routine` 是日常使用的 Python 脚本集合，每个子目录可作为独立子项目。当前已确认的子项目是：

- `explore`：从 iPhone App walkthrough 录屏中提取 UI 页面截图，并整理为 PDF。
- `asr`：按音频复制顺序调用 whisper.cpp 进行语音转文字。

各子项目统一使用 `in/` 放输入素材、`out/` 放生成结果；目录保留但内容不提交。子项目的具体入口、依赖和参数以其 README 与代码为准。

## 相关
- 代码：`explore/appshots.py`
- 子项目说明：`explore/README.md`
- 子项目说明：`asr/README.md`
- 命令：`docs/development/commands.md`
