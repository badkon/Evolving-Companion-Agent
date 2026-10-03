"use strict";
const token = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
let snapshot;
let busy = false;
const result = document.querySelector("#result");
async function api(action, payload) {
  const response = await fetch(`/api/${action}`, {method: payload ? "POST" : "GET", headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"}, ...(payload ? {body: JSON.stringify(payload)} : {})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "请求失败");
  return data;
}
async function load() {
  snapshot = await api("state");
  document.querySelectorAll("[data-config]").forEach(e => {e.value = snapshot.values[e.dataset.config] || "";});
  document.querySelectorAll("[data-character]").forEach(e => {e.value = snapshot.character[e.dataset.character] || "";});
  document.querySelectorAll("[data-secret]").forEach(e => {e.value = ""; e.disabled = true;});
  document.querySelectorAll("[data-mode]").forEach(e => {e.value = "keep"; document.getElementById(`status-${e.dataset.mode}`).textContent = snapshot.secrets[e.dataset.mode] ? "（已配置）" : "（未配置）";});
  document.querySelector("#yaml").value = snapshot.yaml;
  document.querySelector("#identity").textContent = `角色：${snapshot.identity.working_name} · 开发代号：${snapshot.identity.development_id} · 内部 ID：${snapshot.identity.internal_id}`;
  document.querySelector("#overrides").textContent = snapshot.overrides.length ? `这些环境变量优先于文件：${snapshot.overrides.join("、")}` : "当前未发现可编辑字段的环境变量覆盖。";
}
function payload() {
  const values = {}, secrets = {};
  document.querySelectorAll("[data-config]").forEach(e => {values[e.dataset.config] = e.value;});
  document.querySelectorAll("[data-mode]").forEach(e => {secrets[e.dataset.mode] = {mode: e.value, value: e.value === "replace" ? document.querySelector(`[data-secret="${e.dataset.mode}"]`).value : ""};});
  return {values, secrets, version: snapshot.version};
}
async function run(operation) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("button").forEach(e => {e.disabled = true;});
  result.textContent = "处理中，请勿关闭管理器…";
  try { await operation(); } catch (error) {result.textContent = error instanceof Error ? error.message : "操作失败";}
  finally {busy = false; document.querySelectorAll("button").forEach(e => {e.disabled = false;});}
}
document.querySelectorAll("[data-mode]").forEach(e => e.addEventListener("change", () => {const input = document.querySelector(`[data-secret="${e.dataset.mode}"]`); input.disabled = e.value !== "replace"; if (input.disabled) input.value = "";}));
document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => run(async () => {
  let action = button.dataset.action, body;
  if (action === "character" || action === "yaml") {
    body = {version: snapshot.character_version};
    if (action === "yaml") {body.yaml = document.querySelector("#yaml").value; action = "character";}
    else {body.character = {}; document.querySelectorAll("[data-character]").forEach(e => {body.character[e.dataset.character] = e.value;});}
  } else body = payload();
  const response = await api(action, body);
  if (response.ok && (action === "save" || action === "character")) await load();
  result.textContent = response.message;
})));
document.querySelector("#reload").addEventListener("click", () => run(async () => {await load(); result.textContent = "已重新加载。";}));
run(async () => {await load(); result.textContent = "配置已加载。已有密钥不会回显。";});
