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

本轮通过控制逻辑、文件校验、取消及 Server/Studio 协议测试；**尚未执行 llama.cpp 真实 GPU 推理**。Qwen3.5-9B 仍是候选，需单独核对 GGUF 来源、固定构建与公共模型库供给，不将 safetensors 模型直接当作 GGUF。

接口依据：[llama.cpp 官方 Server 文档](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)。
