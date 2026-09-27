# 可选语言模型执行器

本适配器使用已有 `llama-server` 程序与**单文件 GGUF**，不属于 ComfyUI，也不使用 Qwen Image 的编码器。首版支持文本生成、主动提示词优化，暂不声明图片理解、结构化输出、工具调用或分片 GGUF。无需给 Worker 核心安装 Torch、Transformers、Node.js。

## 配置已有环境

1. 在 Linux / WSL2 准备经过单独验证的 CUDA llama.cpp 构建、模型文件及驱动。安装程序、下载模型均为部署者的显式操作；当前管理页没有此执行器的一键安装按钮。
2. 用 `sha256sum /path/to/llama-server /path/to/model.gguf` 获取实际文件摘要。在管理页“环境 → 通用语言模型”填入路径、模型版本、量化、`sha256:...` 标识，或复制 `config/language.example.json` 修改。
3. 保存配置 → 检查环境与模型 → 启用。检查只读文件并核对哈希，不加载模型。启用需要发现 GPU 且确认空闲资源；不提交推理。
4. Server 主动连接 Worker 后，Studio 语言模型列表显示相同规格的执行端数量。默认自动分配，可指定 Worker。API 模型与 Worker 模型共用创作入口，但进入不同队列。
5. 使用少量文本分别验证生成、取消、重启后恢复，以及与 Qwen/IndexTTS/H3 切换后的显存释放，再认定此具体部署通过真实推理验收。

命令行与面板共用控制逻辑：

```bash
zhilume-worker --state /var/lib/zhilume-worker --executor language \
  --executor-action configure --config-file /path/to/language.json
zhilume-worker --state /var/lib/zhilume-worker --executor language --executor-action check
zhilume-worker --state /var/lib/zhilume-worker --executor language --executor-action enable
# 停用可选择等待或取消当前任务
zhilume-worker --state /var/lib/zhilume-worker --executor language --executor-action disable --stop-policy cancel
```

## 进程与资源

执行器启用表示允许接单。每个任务获得物理 GPU 锁后才启动本机回环 `llama-server`，通过本机 HTTP 请求生成；HTTP 协议不改变其 GPU 执行分类。已占用的端口不会被接管。固定模型与程序路径只来自管理配置，不接收任务下发的命令或路径。

任务完成/取消/失败时终止并等待自建进程组退出，检查显存回落后释放 GPU 锁；不能确认则隔离，不切换其他执行器。Linux 启动包装器设置父进程死亡信号，Worker 异常退出时终止推理进程；持久隔离标记仍须在恢复检查中解除。多容器 Worker 必须共享宿主机资源锁目录。

模型、程序 SHA256、量化、工作流修订、上下文与输入输出限制参与规格摘要；路径、端口、GPU 序号不参与。修改程序或模型后必须重新检查。不自动更新二进制、不自动下载权重、不按模型名称静默替换。

`reasoningMode=off|auto` 默认为 off，适合创作与提示词优化；auto 沿用模型模板默认值。该选项参与执行规格匹配。输出 Token 预算覆盖模型输出，默认开启思考的模型可能在给出正文之前耗尽预算；达到上限会明确失败，不归档截断文本。

llama.cpp 共享库发布包的 `llama-server` 可能仅是启动器。此类部署须通过高级 JSON 配置 `runtimeFiles`（逻辑组件名到本机绝对路径），并在 `identity.artifacts` 提供一一对应的 `runtime.<组件名>` SHA256。检查核对实际库文件内容，运行前检查文件是否变更；运行库路径不进入公开规格，组件摘要参与匹配。静态构建可省略。声明了库摘要却未提供校验路径会被拒绝。

2026-09-27 已在上海二 A 的 5090 上通过 Qwen3.5-9B BF16 / llama.cpp b11218 的真实文本、Qwen/H3 提示词优化、取消排队、显存释放及图片执行器切换。Worker 0.12.1 新增运行库校验与明确思考配置。此结果仅适用于受测组合，不代表任意 GGUF、其他量化、视觉理解或其他平台已验证。

受测模型来自云平台公共 Safetensors 目录，在云端使用固定 llama.cpp 转换脚本产生单文件 BF16 GGUF（约 18.4 GB），未重新下载权重。llama.cpp 不能直接把原 Safetensors 目录当成 GGUF；若平台已有经过核验的 GGUF，才可直接配置或链接。转换及依赖准备属于显式部署操作，不能在 Worker 接单时自动触发。转换工具复用了已有 Torch 2.8.0 / Transformers 4.57.6，与 Worker 核心分离。

复测固定程序来源：[llama.cpp b11218](https://github.com/ggml-org/llama.cpp/releases/tag/b11218)。模型来源：[Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)。转换命令为 `python convert_hf_to_gguf.py /path/to/Qwen3.5-9B --outfile /path/to/Qwen3.5-9B-BF16.gguf --outtype bf16`；具体依赖须在独立转换环境检查。本次未实现语言执行器的一键安装或发布云镜像。

接口依据：[llama.cpp 官方 Server 文档](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)。

2026-09-28 已直接读取上海二 A 公共 Qwen3.8-27B Q5_K_M GGUF 完成单卡模型调用及执行器测试，无需转换。加载后短文本约 60 Token/s；当前每任务加载/卸载路径约 8.2–8.7 秒。范围、参数、输出约束问题及 TIME_WAIT 启动检查修正见 [测试报告](qwen38-benchmark-2026-09-28.md)。这不是该模型的 Server/Studio 全链路验收，也不自动替换已有部署配置。
