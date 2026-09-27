export let token = sessionStorage.getItem("worker-management") || "";
export function setToken(value: string) {
  token = value;
  if (value) sessionStorage.setItem("worker-management", value);
  else sessionStorage.removeItem("worker-management");
}
export async function api(path: string, method = "GET", body?: any) {
  const response = await fetch("/management/api/" + path, {
    method,
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(20000),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || "请求失败");
  return data;
}
