import { useState } from "react";
import { api } from "./api";
import { Status } from "./ui";

export function RuntimePanel({
  data,
  refresh,
  reportError,
  busy,
}: {
  data: any;
  refresh: () => Promise<void>;
  reportError: (v: string) => void;
  busy: boolean;
}) {
  const [id, setId] = useState("comfy-main");
  const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState("");
  const runtime = (data.runtimes || []).find((r: any) => r.id === id);
  const config = drafts[id] ||
    runtime?.config || {
      type: "comfyui",
      python: "",
      directory: "",
      modelPathsFile: "",
      port: 8188,
      device: "0",
    };
  const set = (key: string, value: any) =>
    setDrafts((d) => ({ ...d, [id]: { ...config, [key]: value } }));
  async function submit(action: string, policy = "wait") {
    setSending(true);
    reportError("");
    setNotice("");
    try {
      await api(
        `runtimes/${id}${action === "configure" ? "" : "/" + action}`,
        action === "configure" ? "PUT" : "POST",
        action === "configure" ? config : { policy },
      );
      setNotice(
        "操作已提交，完成状态请查看最近部署操作。服务启动后还需检查并启用对应执行器。",
      );
      await refresh();
    } catch (e) {
      reportError((e as Error).message);
    } finally {
      setSending(false);
    }
  }
  return (
    <section className="panel" aria-label="托管推理服务">
      <div className="section-heading">
        <h2>托管推理服务</h2>
        <Status>
          {(
            {
              running: "服务运行中",
              starting: "服务启动中",
              stopped: "服务已停止",
              error: "服务异常",
            } as any
          )[runtime?.state] || "尚未配置"}
        </Status>
      </div>
      <p className="description">
        为没有后台服务的已有 ComfyUI 环境管理进程。Qwen 图片和 H3
        视频可共用一个服务。IndexTTS
        已按任务管理独立进程。保存、检查、启动分别执行，不自动安装或下载模型。
      </p>
      {data.runtimeConfigError && <p role="alert">{data.runtimeConfigError}</p>}
      <div className="form-grid">
        <label>
          运行环境名称
          <input
            aria-label="运行环境名称"
            list="runtime-options"
            value={id}
            onChange={(e) => {
              setId(e.target.value);
              setNotice("");
            }}
          />
          <datalist id="runtime-options">
            {(data.runtimes || []).map((r: any) => (
              <option key={r.id} value={r.id} />
            ))}
          </datalist>
          <small>小写字母、数字和连字符；图片与视频选择同一名称即可复用</small>
        </label>
        <label>
          服务端口
          <input
            aria-label="托管服务端口"
            type="number"
            value={config.port}
            onChange={(e) => set("port", Number(e.target.value))}
          />
          <small>只监听 Worker 本机，默认 8188</small>
        </label>
        <label>
          推理 Python
          <input
            aria-label="托管 Python"
            value={config.python}
            onChange={(e) => set("python", e.target.value)}
          />
        </label>
        <label>
          ComfyUI 程序目录
          <input
            aria-label="托管 ComfyUI 目录"
            value={config.directory}
            onChange={(e) => set("directory", e.target.value)}
          />
        </label>
        <label>
          模型搜索路径文件
          <input
            aria-label="模型搜索路径文件"
            value={config.modelPathsFile}
            onChange={(e) => set("modelPathsFile", e.target.value)}
          />
          <small>
            可选，extra_model_paths YAML 文件；留空使用 ComfyUI 默认模型目录
          </small>
        </label>
        <label>
          可见 GPU 序号
          <input
            aria-label="托管 GPU 序号"
            value={config.device}
            onChange={(e) => set("device", e.target.value)}
          />
        </label>
      </div>
      <p className="description">
        {runtime?.reason ||
          "先准备 Linux / WSL2 推理环境，再填写此机器上的绝对路径。"}
      </p>
      <div className="actions">
        <button
          disabled={busy || sending}
          onClick={() => void submit("configure")}
        >
          保存服务配置
        </button>
        <button
          disabled={busy || sending || !runtime}
          onClick={() => void submit("check")}
        >
          检查程序路径
        </button>
        <button
          disabled={busy || sending || !runtime}
          onClick={() => void submit("start")}
        >
          启动推理服务
        </button>
        <button
          disabled={busy || sending || !runtime}
          onClick={() => void submit("stop")}
        >
          等待任务后停止服务
        </button>
        <button
          disabled={busy || sending || !runtime}
          onClick={() => void submit("stop", "cancel")}
        >
          取消任务并停止服务
        </button>
      </div>
      <small>
        停止共享服务会停用所有关联执行器；复用的外部服务不会被终止。重启 Worker
        后需重新启动托管服务，再启用执行器。
      </small>
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
