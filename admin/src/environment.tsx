import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Code,
  FileText,
  LoaderCircle,
  Plus,
  Save,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { api } from "./api";
import { executorNames, Status, statusNames } from "./ui";
import { RuntimePanel } from "./runtimes";

type Config = Record<string, any>;
function parseConfig(raw: string): Config {
  const c = JSON.parse(raw);
  const object = (v: any) => v && typeof v === "object" && !Array.isArray(v);
  if (!object(c)) throw Error("配置须为 JSON 对象");
  if (
    c.profiles !== undefined &&
    (!Array.isArray(c.profiles) || c.profiles.some((p: any) => !object(p)))
  )
    throw Error("profiles 须为模型对象列表");
  for (const p of [c, ...(c.profiles || [])]) {
    if (
      p.models !== undefined &&
      (!object(p.models) ||
        Object.values(p.models).some((v) => typeof v !== "string"))
    )
      throw Error("模型文件须为文本映射");
    if (
      p.identity !== undefined &&
      (!object(p.identity) ||
        (p.identity.artifacts !== undefined && !object(p.identity.artifacts)))
    )
      throw Error("identity 与 artifacts 须为对象");
    if (
      p.sizes !== undefined &&
      (!Array.isArray(p.sizes) ||
        p.sizes.some((v: any) => !Array.isArray(v) || v.length !== 2))
    )
      throw Error("sizes 须为宽高对列表");
    if (p.frames !== undefined && !Array.isArray(p.frames))
      throw Error("frames 须为帧数列表");
  }
  return c;
}

const componentNames: Record<string, string> = {
  diffusion: "主模型",
  clip: "文本编码器",
  vae: "图像 / 视频 VAE",
  audioVae: "音频 VAE",
  tts: "语音模型组",
};
function Field({
  label,
  value,
  change,
  type = "text",
  hint,
}: {
  label: string;
  value: any;
  change: (v: any) => void;
  type?: string;
  hint?: string;
}) {
  return (
    <label>
      {label}
      <input
        type={type}
        aria-label={label}
        value={value ?? ""}
        onChange={(e) =>
          change(
            type === "number"
              ? e.target.value === ""
                ? ""
                : Number(e.target.value)
              : e.target.value,
          )
        }
      />
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Identity({
  value = {},
  keys,
  change,
}: {
  value?: Config;
  keys: string[];
  change: (v: Config) => void;
}) {
  return (
    <details className="identity-fields" open>
      <summary>
        模型版本与组件标识 <span className="muted">用于跨 Worker 精确匹配</span>
      </summary>
      <p className="description">
        填写实际部署的权重版本和量化。组件使用 sha256:摘要或已固定的
        revision:版本；不以文件名或本机路径代替。
      </p>
      <div className="form-grid">
        <Field
          label="权重版本"
          value={value.revision}
          change={(v) => change({ ...value, revision: v })}
        />
        <Field
          label="量化格式"
          value={value.quantization}
          change={(v) => change({ ...value, quantization: v })}
          hint="例如 fp8、int8；混合量化请明确描述"
        />
      </div>
      {keys.map((k) => (
        <Field
          key={k}
          label={`${componentNames[k] || k}固定标识`}
          value={value.artifacts?.[k]}
          change={(v) =>
            change({ ...value, artifacts: { ...value.artifacts, [k]: v } })
          }
        />
      ))}
    </details>
  );
}
function ModelProfile({
  profile,
  change,
  remove,
  kind,
}: {
  profile: Config;
  change: (v: Config) => void;
  remove: () => void;
  kind: string;
}) {
  const set = (key: string, value: any) => change({ ...profile, [key]: value });
  return (
    <section className="profile-card">
      <div className="section-heading">
        <h3>{profile.modelId}</h3>
        <button
          type="button"
          className="icon-button danger-quiet"
          aria-label={`移除 ${profile.modelId}`}
          onClick={remove}
        >
          <Trash2 size={16} />
        </button>
      </div>
      <p className="description">
        填写 ComfyUI
        模型列表中的相对文件名，支持子目录；公共模型软链接应先在部署机器准备好。
      </p>
      <div className="form-grid">
        {Object.keys(profile.models || {}).map((k) => (
          <Field
            key={k}
            label={`${componentNames[k] || k}文件`}
            value={profile.models[k]}
            change={(v) => set("models", { ...profile.models, [k]: v })}
          />
        ))}
      </div>
      <details>
        <summary>能力限制与默认参数</summary>
        <div className="form-grid">
          <Field
            label="默认步数"
            type="number"
            value={profile.defaultSteps}
            change={(v) => set("defaultSteps", v)}
          />
          {kind === "image" && (
            <>
              <Field
                label="最大边长"
                type="number"
                value={profile.maxSize}
                change={(v) => set("maxSize", v)}
                hint="512–2048，32 的倍数"
              />
              {profile.modelId === "qwen-image-2.1" && (
                <>
                  <Field
                    label="最多参考图"
                    type="number"
                    value={profile.maxReferences ?? 4}
                    change={(v) => set("maxReferences", v)}
                  />
                  <Field
                    label="参考图处理尺寸"
                    type="number"
                    value={profile.referenceResolution ?? 1024}
                    change={(v) => set("referenceResolution", v)}
                  />
                </>
              )}
            </>
          )}
        </div>
        {kind === "video" && (
          <>
            <h4>允许的输出尺寸</h4>
            {(profile.sizes || []).map((size: number[], i: number) => (
              <div className="array-row" key={i}>
                <Field
                  label={`尺寸 ${i + 1} 宽度`}
                  type="number"
                  value={size[0]}
                  change={(v) =>
                    set(
                      "sizes",
                      profile.sizes.map((s: number[], j: number) =>
                        j === i ? [v, s[1]] : s,
                      ),
                    )
                  }
                />
                <Field
                  label={`尺寸 ${i + 1} 高度`}
                  type="number"
                  value={size[1]}
                  change={(v) =>
                    set(
                      "sizes",
                      profile.sizes.map((s: number[], j: number) =>
                        j === i ? [s[0], v] : s,
                      ),
                    )
                  }
                />
                <button
                  onClick={() =>
                    set(
                      "sizes",
                      profile.sizes.filter((_: any, j: number) => i !== j),
                    )
                  }
                >
                  移除
                </button>
              </div>
            ))}
            <button
              onClick={() =>
                set("sizes", [...(profile.sizes || []), [512, 288]])
              }
            >
              添加尺寸
            </button>
            <h4>允许的帧数</h4>
            {(profile.frames || []).map((frame: number, i: number) => (
              <div className="array-row" key={i}>
                <Field
                  label={`帧数 ${i + 1}`}
                  type="number"
                  value={frame}
                  change={(v) =>
                    set(
                      "frames",
                      profile.frames.map((n: number, j: number) =>
                        j === i ? v : n,
                      ),
                    )
                  }
                />
                <button
                  onClick={() =>
                    set(
                      "frames",
                      profile.frames.filter((_: any, j: number) => j !== i),
                    )
                  }
                >
                  移除
                </button>
              </div>
            ))}
            <button
              onClick={() => set("frames", [...(profile.frames || []), 124])}
            >
              添加帧数
            </button>
            <div className="form-grid">
              {["image", "video", "audio"].map((k) => (
                <Field
                  key={k}
                  label={`最多参考${{ image: "图片", video: "视频", audio: "音频" }[k]}`}
                  type="number"
                  value={profile.referenceLimits?.[k]}
                  change={(v) =>
                    set("referenceLimits", {
                      ...profile.referenceLimits,
                      [k]: v,
                    })
                  }
                />
              ))}
            </div>
          </>
        )}
      </details>
      <Identity
        value={profile.identity}
        keys={Object.keys(profile.models || {})}
        change={(v) => set("identity", v)}
      />
    </section>
  );
}
export function CheckResults({ checks = [] }: { checks: any[] }) {
  if (!checks.length) return null;
  return (
    <div className="check-results" aria-label="逐项检查结果">
      {checks.map((c) => (
        <div className={`check-item ${c.state}`} key={c.id}>
          {c.state === "checking" ? (
            <LoaderCircle size={17} className="spin" />
          ) : c.state === "passed" ? (
            <CheckCircle2 size={17} />
          ) : (
            <CircleAlert size={17} />
          )}
          <div>
            <strong>
              <span className="check-title">{c.title}</span>
              <span>
                {
                  {
                    checking: "检查中",
                    passed: "通过",
                    failed: "未通过",
                    skipped: "未检查",
                  }[c.state as string]
                }
              </span>
            </strong>
            <p>{c.detail}</p>
            {c.remedy && <small>处理建议：{c.remedy}</small>}
          </div>
        </div>
      ))}
    </div>
  );
}
const actionNames: Record<string, string> = {
  configure: "保存配置",
  check: "环境检查",
  enable: "检查并启用",
  disable: "停用并释放资源",
  install: "安装依赖",
  start: "启动推理服务",
  stop: "停止推理服务",
};
export function OperationList({ operations }: { operations: any[] }) {
  const [cancelError, setCancelError] = useState("");
  if (!operations.length) return null;
  return (
    <section className="panel">
      <h2>最近部署操作</h2>
      {cancelError && <p className="inline-alert">{cancelError}</p>}
      {operations
        .slice(-8)
        .reverse()
        .map((o) => {
          const [kind, action] = o.name.split(":");
          return (
            <div className="operation-row" key={o.id}>
              <div>
                <strong>
                  {kind === "runtime" ? "运行服务" : executorNames[kind] || kind} ·{" "}
                  {actionNames[action] || action}
                </strong>
                <small>
                  {o.startedAt ? new Date(o.startedAt).toLocaleString() : ""}
                  {o.finishedAt
                    ? " → " + new Date(o.finishedAt).toLocaleTimeString()
                    : ""}
                </small>
              </div>
              <Status state={o.state}>
                {o.state === "running" ? (
                  <LoaderCircle size={12} className="spin" />
                ) : null}
                {statusNames[o.state] || o.state}
              </Status>
              {o.state === "running" && (
                <p className="description">
                  {o.stage || "正在执行，管理接口保持可用。检查明细见对应引擎的逐项结果。"}
                </p>
              )}
              {action === "install" && o.state === "running" && (
                <button disabled={o.cancelRequested} onClick={() => {
                  setCancelError("");
                  void api(`operations/${o.id}/cancel`, "POST", {}).catch(e => setCancelError(e.message));
                }}>{o.cancelRequested ? "正在取消…" : "取消安装"}</button>
              )}
              {o.result?.next && <p className="description">{o.result.next}</p>}
              {(o.error || o.result?.reason) && (
                <p
                  className={
                    o.state === "failed" ? "inline-alert" : "description"
                  }
                >
                  {o.error || o.result.reason}
                </p>
              )}
            </div>
          );
        })}
    </section>
  );
}
export function ExecutorLogs() {
  const [kind, setKind] = useState("image"),
    [logs, setLogs] = useState<any[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function load(k: string) {
    setBusy(true);
    setError("");
    try {
      setLogs(await api(`executors/${k}/logs`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load(kind);
  }, [kind]);
  return (
    <section className="executor-logs">
      <h2>
        <FileText size={18} />
        引擎操作日志
      </h2>
      <div className="form-toolbar">
        <label>
          日志来源
          <select
            value={kind}
            onChange={(e) => {
              setLogs([]);
              setKind(e.target.value);
            }}
          >
            {Object.entries(executorNames).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <button disabled={busy} onClick={() => void load(kind)}>
          刷新日志
        </button>
      </div>
      <p className="description">
        最近 60
        条脱敏部署事件，记录开始、结果与检查明细；不包含原始推理控制台输出。
      </p>
      {error && <p role="alert">{error}</p>}
      {busy ? (
        <p>读取日志中…</p>
      ) : logs.length ? (
        <pre>{logs.map((l) => JSON.stringify(l, null, 2)).join("\n")}</pre>
      ) : (
        <p className="muted">当前引擎暂无部署日志</p>
      )}
    </section>
  );
}
export function EnvironmentPanel({
  data,
  refresh,
  reportError,
  operations,
}: {
  data: any;
  refresh: () => Promise<void>;
  reportError: (v: string) => void;
  operations: any[];
}) {
  const [kind, setKind] = useState("image"),
    [templates, setTemplates] = useState<Config | null>(null),
    [drafts, setDrafts] = useState<Record<string, Config>>({}),
    [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [advanced, setAdvanced] = useState(false),
    [raw, setRaw] = useState(""),
    [notice, setNotice] = useState(""),
    [saving, setSaving] = useState(false);
  const [selectedModel, setSelectedModel] = useState(""),
    [installDir, setInstallDir] = useState(""),
    [python, setPython] = useState(""),
    [plan, setPlan] = useState<any>(null);
  const currentInstall = useRef("");
  currentInstall.current = JSON.stringify({kind, directory: installDir, python});
  async function previewInstall() {
    const snapshot = currentInstall.current;
    try {
      const result = await api("install", "POST", JSON.parse(snapshot));
      if (currentInstall.current === snapshot) setPlan(result);
    } catch (e) {
      if (currentInstall.current === snapshot) reportError((e as Error).message);
    }
  }
  const [pendingSave, setPendingSave] = useState<{
    id: string;
    kind: string;
    value: Config;
  } | null>(null);
  useEffect(() => {
    if (!pendingSave) return;
    const operation = operations.find((o) => o.id === pendingSave.id);
    if (!operation || operation.state === "running") return;
    if (operation.state === "succeeded") {
      setDirty((d) => ({
        ...d,
        [pendingSave.kind]:
          JSON.stringify(drafts[pendingSave.kind]) !==
          JSON.stringify(pendingSave.value),
      }));
      setNotice("配置已保存并停用。请检查环境，再单独启用。");
    } else {
      setDirty((d) => ({ ...d, [pendingSave.kind]: true }));
      reportError(
        operation.error || operation.result?.reason || "保存失败，请修正后重试",
      );
    }
    setPendingSave(null);
  }, [operations, pendingSave]);
  const executor = data.executors.find((x: any) => x.id === kind);
  const saved = executor?.config || {};
  const config = drafts[kind] ?? saved;
  const working = saving || operations.some((o) => o.state === "running");
  useEffect(() => {
    void api("environment-templates").then(setTemplates, (e) =>
      reportError(e.message),
    );
  }, []);
  const update = (next: Config) => {
    setDrafts((d) => ({ ...d, [kind]: next }));
    setDirty((d) => ({ ...d, [kind]: true }));
    setNotice("");
  };
  function switchKind(next: string) {
    if (advanced) {
      try {
        const v = parseConfig(raw);
        if (!v || Array.isArray(v) || typeof v !== "object") throw Error();
        update(v);
      } catch {
        reportError("请先修正 JSON，或使用“放弃当前修改”恢复已保存配置。");
        return;
      }
    }
    setAdvanced(false);
    setKind(next);
    setSelectedModel("");
    setPlan(null);
    setNotice("");
  }
  function toggleAdvanced() {
    if (advanced) {
      try {
        const v = parseConfig(raw);
        if (!v || Array.isArray(v) || typeof v !== "object") throw Error();
        update(v);
        setAdvanced(false);
      } catch {
        reportError("JSON 须为有效对象");
      }
    } else {
      setRaw(JSON.stringify(config, null, 2));
      setAdvanced(true);
    }
  }
  function prepare() {
    if (!templates) return;
    const next = structuredClone(templates[kind]);
    if (!["speech", "language"].includes(kind)) next.profiles = [];
    update(next);
  }
  async function save() {
    setSaving(true);
    reportError("");
    try {
      const value = advanced ? parseConfig(raw) : config;
      if (!value || typeof value !== "object" || Array.isArray(value))
        throw Error("配置须为对象");
      const result = await api(`executors/${kind}/config`, "PUT", value);
      setDrafts((d) => ({ ...d, [kind]: value }));
      setPendingSave({ id: result.operationId, kind, value });
      setNotice("已提交保存。保存完成后，请检查环境，再单独启用。");
      await refresh();
    } catch (e) {
      reportError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const set = (k: string, v: any) => update({ ...config, [k]: v });
  const choices = templates?.[kind]?.profiles || [];
  const modelChoice = selectedModel || choices[0]?.modelId || "";
  return (
    <>
      <RuntimePanel
        data={data}
        refresh={refresh}
        reportError={reportError}
        busy={working}
      />
      <section className="panel">
        <div className="section-heading">
          <h2>
            <SlidersHorizontal size={18} />
            配置已有环境
          </h2>
          <Status>
            {dirty[kind]
              ? "有未保存修改"
              : Object.keys(saved).length
                ? "已保存配置"
                : "尚未配置"}
          </Status>
        </div>
        <div className="form-toolbar">
          <label>
            生成引擎
            <select
              aria-label="生成引擎"
              value={kind}
              onChange={(e) => switchKind(e.target.value)}
            >
              {Object.entries(executorNames).map(([k, v]) => (
                <option value={k} key={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <Status>
            {kind === "language" ? "独立 llama.cpp 进程" : kind === "speech" ? "独立 Python 进程" : "ComfyUI 服务"}
          </Status>
        </div>
        <p className="description">
          {kind === "language" ? "语言模型采用独立受管进程，不属于 ComfyUI；可先配置和诊断，再单独验收 GPU 推理。" : kind === "speech"
            ? "复用已有 IndexTTS 环境。填写的是 Worker 部署机器上的路径，不是浏览器所在电脑的路径。"
            : "Qwen 图片和 H3 视频可复用已有专用服务，或选择上方配置的托管服务。服务运行和执行器接单分别管理；不会因保存配置而启动或安装推理环境。"}
        </p>
        <div className="actions">
          <button disabled={!templates || working} onClick={prepare}>
            填入配置模板
          </button>
          <button onClick={toggleAdvanced}>
            <Code size={16} />
            {advanced ? "返回表单" : "高级 JSON"}
          </button>
          <button
            onClick={() => {
              setDrafts((d) => ({ ...d, [kind]: structuredClone(saved) }));
              setDirty((d) => ({ ...d, [kind]: false }));
              setAdvanced(false);
              setNotice("已恢复服务端保存的配置。");
            }}
          >
            放弃当前修改
          </button>
        </div>
        {advanced ? (
          <label>
            部署配置 JSON
            <textarea
              rows={18}
              spellCheck={false}
              value={raw}
              onChange={(e) => {
                setRaw(e.target.value);
                setDirty((d) => ({ ...d, [kind]: true }));
              }}
            />
          </label>
        ) : (
          <>
            {kind === "language" ? <>
              <p className="description">使用已有 llama-server 与单文件 GGUF。任务持有 GPU 锁后才加载模型，结束或取消后停止进程。首版纯文本；检查通过不代表真实推理通过。</p>
              <div className="form-grid">{[["binary","llama-server 程序"],["modelFile","GGUF 模型文件"],["modelId","模型标识"],["device","GPU 设备序号"]].map(([k,label]) => <Field key={k} label={label} value={config[k]} change={v=>set(k,v)}/>)}
              {[["port","本地服务端口"],["contextSize","上下文容量"],["maxInputCharacters","输入字符上限"],["maxOutputTokens","输出 Token 上限"]].map(([k,label])=><Field key={k} label={label} type="number" value={config[k]} change={v=>set(k,v)}/>)}</div>
              <div className="form-grid">{[["revision","模型版本"],["quantization","量化规格"]].map(([k,label])=><Field key={k} label={label} value={config.identity?.[k]} change={v=>set("identity",{...config.identity,[k]:v})}/>)}
              {["model","binary"].map(k=><Field key={k} label={k === "model" ? "模型 SHA256 标识" : "程序 SHA256 标识"} value={config.identity?.artifacts?.[k]} change={v=>set("identity",{...config.identity,artifacts:{...config.identity?.artifacts,[k]:v}})}/>)}</div>
              <p className="description">共享库构建需在高级 JSON 中填写 runtimeFiles 路径及对应 runtime.* SHA256，避免只校验启动器。已配置 {Object.keys(config.runtimeFiles || {}).length} 个运行库；这些路径不参与跨机器模型匹配。</p>
              <label>思考模式<select aria-label="思考模式" value={config.reasoningMode || "off"} onChange={e=>set("reasoningMode",e.target.value)}><option value="off">关闭（适合创作与提示词优化）</option><option value="auto">使用模型默认值</option></select></label>
            </> : kind === "speech" ? (
              <>
                <div className="form-grid">
                  {[
                    ["python", "Python 程序"],
                    ["repository", "IndexTTS 程序目录"],
                    ["modelDirectory", "模型目录"],
                    ["workingDirectory", "工作目录"],
                    ["ffmpeg", "FFmpeg 程序"],
                    ["device", "GPU 设备"],
                  ].map(([key, label]) => (
                    <Field
                      key={key}
                      label={label}
                      value={
                        config[key] ?? (key === "device" ? "cuda:0" : undefined)
                      }
                      change={(v) => set(key, v)}
                      hint={
                        key === "device"
                          ? "例如 cuda:0"
                          : key === "workingDirectory"
                            ? "选填，留空使用 IndexTTS 程序目录"
                            : "部署机器上的绝对路径"
                      }
                    />
                  ))}
                </div>
                <div className="form-grid">
                  <Field
                    label="最大文本字数"
                    type="number"
                    value={config.maxTextCharacters ?? 1000}
                    change={(v) => set("maxTextCharacters", v)}
                  />
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={!!config.enableEmotionText}
                      onChange={(e) =>
                        set("enableEmotionText", e.target.checked)
                      }
                    />
                    启用文本情绪控制<small>需额外准备对应情绪模型</small>
                  </label>
                </div>
                <Identity
                  value={config.identity}
                  keys={["tts"]}
                  change={(v) => set("identity", v)}
                />
              </>
            ) : (
              <>
                <label>
                  服务部署方式
                  <select
                    aria-label="服务部署方式"
                    value={config.runtimeId || ""}
                    onChange={(e) => set("runtimeId", e.target.value)}
                  >
                    <option value="">复用已有服务（自行管理启停）</option>
                    {(data.runtimes || []).map((r: any) => (
                      <option key={r.id} value={r.id}>
                        Worker 托管 · {r.id}
                      </option>
                    ))}
                  </select>
                </label>
                {!config.runtimeId && (
                  <Field
                    label="ComfyUI 服务地址"
                    value={config.url}
                    change={(v) => set("url", v)}
                    hint="例如 http://127.0.0.1:8188，地址相对于 Worker 所在机器"
                  />
                )}
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={!!config.exclusive}
                    onChange={(e) => set("exclusive", e.target.checked)}
                  />
                  专用于此 Worker 的 ComfyUI 服务
                  <small>
                    允许本 Worker 的 Qwen / H3
                    共用；不要与外部任务共享，以免取消和卸载互相影响。
                  </small>
                </label>
                <div className="form-grid">
                  <Field
                    label="执行超时（秒）"
                    type="number"
                    value={
                      config.timeoutSeconds ?? (kind === "video" ? 3600 : 1800)
                    }
                    change={(v) => set("timeoutSeconds", v)}
                  />
                  <Field
                    label="卸载后显存上限（MiB）"
                    type="number"
                    value={config.idleVramLimitMiB ?? 1536}
                    change={(v) => set("idleVramLimitMiB", v)}
                    hint="256–4096，根据专用服务的空载基线设置"
                  />
                  {kind === "video" &&
                    ["ffmpeg", "ffprobe"].map((k) => (
                      <Field
                        key={k}
                        label={`${k.toUpperCase()} 程序`}
                        value={config[k]}
                        change={(v) => set(k, v)}
                        hint="Worker 部署机器上的绝对路径"
                      />
                    ))}
                </div>
                <div className="section-heading model-heading">
                  <h3>已部署模型</h3>
                  <div className="actions">
                    <select
                      aria-label="待添加模型"
                      value={modelChoice}
                      onChange={(e) => setSelectedModel(e.target.value)}
                    >
                      {choices.map((p: Config) => (
                        <option key={p.modelId}>{p.modelId}</option>
                      ))}
                    </select>
                    <button
                      disabled={!choices.length}
                      onClick={() =>
                        set("profiles", [
                          ...(config.profiles || []),
                          structuredClone(
                            choices.find(
                              (p: Config) => p.modelId === modelChoice,
                            ),
                          ),
                        ])
                      }
                    >
                      <Plus size={16} />
                      添加模型
                    </button>
                  </div>
                </div>
                {!(config.profiles || []).length && (
                  <p className="muted">
                    添加实际已经部署的模型。模板只填写参数，不下载模型。
                  </p>
                )}
                {(config.profiles || []).map((p: Config, i: number) => (
                  <ModelProfile
                    key={i}
                    profile={p}
                    kind={kind}
                    change={(v) =>
                      set(
                        "profiles",
                        config.profiles.map((old: Config, j: number) =>
                          j === i ? v : old,
                        ),
                      )
                    }
                    remove={() =>
                      set(
                        "profiles",
                        config.profiles.filter((_: any, j: number) => j !== i),
                      )
                    }
                  />
                ))}
              </>
            )}
          </>
        )}
        <div className="actions">
          <button
            className="primary"
            disabled={working || !!data.tasks.length}
            onClick={() => void save()}
          >
            <Save size={16} />
            保存配置并停用
          </button>
          <span className="muted">
            仅保存配置，不安装依赖、不下载权重、不运行推理。
          </span>
        </div>
        {notice && (
          <p role="status" className="notice">
            {notice}
          </p>
        )}
      </section>
      <section className="panel">
        <h2>检查与启用</h2>
        <p className="description">
          检查已保存的配置。环境检查不会生成内容；真实推理是否通过单独记录。
        </p>
        <div className="actions">
          <button
            disabled={working || dirty[kind] || !!data.tasks.length}
            onClick={() =>
              void api(`executors/${kind}/check`, "POST", {}).then(
                refresh,
                (e) => reportError(e.message),
              )
            }
          >
            检查环境与模型
          </button>
          <button
            disabled={working || dirty[kind] || !!data.tasks.length}
            onClick={() =>
              void api(`executors/${kind}/enable`, "POST", {}).then(
                refresh,
                (e) => reportError(e.message),
              )
            }
          >
            检查并启用
          </button>
          <Status state={executor.state}>{statusNames[executor.state]}</Status>
        </div>
        {executor.reason && <p className="description">{executor.reason}</p>}
        <CheckResults checks={executor.checks || []} />
        {!executor.checks?.length && (
          <p className="muted">尚无逐项检查结果。保存配置后运行检查。</p>
        )}
      </section>
      {kind !== "language" && <details className="panel">
        <summary>安装独立标准环境（Linux / WSL2）</summary>
        <p className="description">
          已有环境无需重复安装。安装是独立操作，不下载模型、不启动 GPU。
          Qwen/H3 共用一个 ComfyUI 环境，选择同一托管服务即可。
        </p>
        <p className="description">{kind === "speech" ? "IndexTTS 标准方案使用 Python 3.11。" : "ComfyUI 标准方案使用 Python 3.12。"} 需要 Linux x86_64、Git、uv 及至少 24 GiB 空闲磁盘；不会自动安装系统工具或 Python。</p>
        <p className="description">本版标准依赖方案尚待干净环境 GPU 验收；已有环境托管实测不代表此安装方案已验证。</p>
        <Field
          label="新安装目录"
          value={installDir}
          change={(v) => {
            setInstallDir(v);
            setPlan(null);
          }}
        />
        <Field
          label="已有 Python 绝对路径"
          value={python}
          change={(v) => {
            setPython(v);
            setPlan(null);
          }}
        />
        <button
          disabled={working}
          onClick={() => void previewInstall()}
        >
          查看安装计划
        </button>
        {plan && (
          <>
            <div className="description">
              <p>环境：{plan.profile} · Python {plan.pythonVersion} · CUDA 12.8</p>
              <p>安装到：{plan.directory}</p>
              <p>依次执行：{plan.steps?.join(" → ")}</p>
              <p>完成后仍需配置模型、检查并显式启用。安装失败保留记录，使用新目录重试。</p>
              <details><summary>版本与依赖锁详情</summary><pre>{JSON.stringify(plan, null, 2)}</pre></details>
            </div>
            <button
              disabled={working}
              onClick={() =>
                void api("install", "POST", {
                  kind: plan.kind,
                  directory: plan.directory,
                  python: plan.python,
                  planId: plan.planId,
                  execute: true,
                }).then(refresh, (e) => reportError(e.message))
              }
            >
              执行依赖安装
            </button>
          </>
        )}
      </details>}
    </>
  );
}
