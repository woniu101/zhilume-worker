import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

type Theme = "dark" | "light" | "system";
const saved = localStorage.getItem("zhilume.theme");
const initial: Theme = saved === "light" || saved === "system" ? saved : "dark";
function applyTheme(mode: Theme) {
  document.documentElement.dataset.theme =
    mode === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : mode;
}
applyTheme(initial);

export function ThemePicker() {
  const [mode, setMode] = useState<Theme>(initial);
  useEffect(() => {
    applyTheme(mode);
    localStorage.setItem("zhilume.theme", mode);
    const media = matchMedia("(prefers-color-scheme: dark)");
    const update = () => applyTheme(mode);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [mode]);
  return (
    <div className="theme-picker" role="group" aria-label="外观主题">
      {(
        [
          { id: "light", title: "浅色", Icon: Sun },
          { id: "dark", title: "深色", Icon: Moon },
          { id: "system", title: "跟随系统", Icon: Monitor },
        ] as const
      ).map(({ id, title, Icon }) => (
        <button
          key={id}
          aria-label={`${title}主题`}
          title={title}
          aria-pressed={mode === id}
          onClick={() => setMode(id)}
        >
          <Icon size={16} />
          <span>{title}</span>
        </button>
      ))}
    </div>
  );
}

export function Brand() {
  return (
    <div className="brand">
      <svg width="36" height="36" viewBox="0 0 512 512" aria-hidden="true">
        <rect x="16" y="16" width="480" height="480" rx="112" fill="#202124" />
        <rect
          x="25"
          y="25"
          width="462"
          height="462"
          rx="104"
          fill="none"
          stroke="#44464c"
          strokeWidth="4"
        />
        <path
          d="M142 150h228L152 362h220"
          fill="none"
          stroke="#edf0f6"
          strokeWidth="40"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M154 244h82m50 24h72"
          fill="none"
          stroke="#8aaaf0"
          strokeWidth="24"
          strokeLinecap="round"
        />
        <rect
          x="334"
          y="334"
          width="152"
          height="152"
          rx="46"
          fill="#8aaaf0"
          stroke="#202124"
          strokeWidth="10"
        />
        <rect
          x="380"
          y="380"
          width="60"
          height="60"
          rx="8"
          fill="none"
          stroke="#202124"
          strokeWidth="10"
        />
        <path
          d="M392 362v18m36-18v18m-36 60v18m36-18v18m-66-66h18m-18 36h18m60-36h18m-18 36h18"
          stroke="#202124"
          strokeWidth="8"
        />
      </svg>
      <span>
        Zhilume<small>WORKER</small>
      </span>
    </div>
  );
}

export const executorNames: Record<string, string> = {
  image: "Qwen / ComfyUI",
  speech: "IndexTTS",
  video: "H3",
};
export const statusNames: Record<string, string> = {
  disabled: "已停用",
  checking: "检查中",
  checked: "检查通过",
  ready: "可接单",
  error: "故障",
  draining: "等待停用",
  running: "执行中",
  succeeded: "完成",
  failed: "失败",
};
export function Status({
  state,
  children,
}: {
  state?: string;
  children: React.ReactNode;
}) {
  return (
    <span className={`status ${state || ""}`}>
      <i />
      {children}
    </span>
  );
}
