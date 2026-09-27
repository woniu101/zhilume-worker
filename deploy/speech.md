# IndexTTS 2.5 独立运行环境

本执行器不依赖 ComfyUI。Worker 单并发不负责清理其他推理服务的显存缓存；同卡首次验收应停下 ComfyUI，只启用语音。普通视频截取、抽音轨仍在 Studio Electron / Server 执行。

## 安装

先准备 Python 3.10、Git、uv 和 FFmpeg。以下命令安装固定上游依赖，不下载模型；使用独立目录，不覆盖已有环境。可以先用无卡实例准备依赖，再切换 GPU。

```bash
mkdir /opt/zhilume-indextts
git -C /opt/zhilume-indextts init
git -C /opt/zhilume-indextts remote add origin https://github.com/index-tts/index-tts.git
GIT_LFS_SKIP_SMUDGE=1 git -C /opt/zhilume-indextts fetch --depth 1 origin ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c
GIT_LFS_SKIP_SMUDGE=1 git -C /opt/zhilume-indextts checkout --detach FETCH_HEAD
cd /opt/zhilume-indextts
uv sync --frozen --no-dev --python 3.10
```

2026-09-27 上海二 A 实测环境为 Python 3.10.15、torch/torchaudio 2.8.0+cu128、transformers 4.52.1、bf16；未启用 DeepSpeed 或自定义 CUDA kernel。首次中文文本规范化会构建 FST 缓存，首次启动可能更慢。上游包名版本仍显示 indextts 2.0.0，应核对 Git 提交及 infer_v2_5.py，不能用包名版本判断 2.5。

## 精确链接公共模型

回到 Worker 仓库。下列清单来自上海二 A 当日实际 `/model` 挂载，不代表其他区域或后续镜像保证具有相同文件。先只读检查 24 个准确文件，再显式创建链接；缺失、歧义或冲突会失败，不覆盖文件、不递归链接整个库。

```bash
uv run --frozen zhilume-prepare --manifest deploy/speech-models.shanghai.example.json --model-root /model --destination /opt/zhilume-indextts-models
uv run --frozen zhilume-prepare --manifest deploy/speech-models.shanghai.example.json --model-root /model --destination /opt/zhilume-indextts-models --apply-links
```

包含 IndexTeam/IndexTTS-2.5、qwen0.6bemo4-merge、facebook/w2v-bert-2.0、nvidia/bigvgan_v2_22khz_80band_256x，以及 gongjy/campplus 的 `campplus_cn_common.pt`。最后一项链接名为上游要求的 `.bin`；已经严格加载校验所有参数键/形状，不是仅按文件扩展名判断兼容。所有权重留在只读公共库，镜像只保存软链接；缺模型时由部署者处理，运行器不会自动下载。

复制 `config/indextts.example.json` 为本地配置，指定此环境的 Python、仓库、modelDirectory 和 FFmpeg 绝对路径。需要文字情绪时设置 `enableEmotionText: true`。配置与状态目录不要提交仓库或打进公开镜像。

```bash
uv run --frozen zhilume-worker --speech-config config/indextts.local.json --enable-speech-execution
```

Worker 默认绑定本机；受保护网络入口按部署环境指定 `--host`。Server 主动发起连接、传入素材并拉取结果，不需要公网回调地址。预检成功只代表可以试运行，profile 仍为 `unverified`，真实 GPU 结果须单独验收。

## 重跑真实验收

在 Server 仓库先 `npm run build`，设置 `ZHILUME_WORKER_ADDRESS`、`ZHILUME_WORKER_TOKEN`、`ZHILUME_SPEAKER_FILE`；参考 WAV 至少 5 秒。可另外提供 `ZHILUME_EMOTION_FILE` 和 `ZHILUME_ACCEPTANCE_DIR`。测试使用独立 Server 数据目录，首次接入需使用尚未绑定其他 Server 的 Worker 状态目录。不要把密钥写入脚本。

```bash
node scripts/accept-speech-cloud.mjs follow --execute
# 其他模式：fast、slow、vector、text、reference、cancel、after-cancel
```

该脚本确实提交 GPU 工作，运行前告知资源所有者。Server 不监听入站端口；脚本检验幂等提交、参考角色、输出 SHA-256、结果归档及来源参数，报告和 WAV 写入指定目录。取消测试默认在进入加载/推理阶段 20 秒后提交取消，可用 `ZHILUME_CANCEL_DELAY_MS` 调整；应同时检查 GPU 进程是否退出，再运行 after-cancel。主观音色相似度和情绪表现不能由 WAV 存在或时长自动判定。
