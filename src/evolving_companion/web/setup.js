"use strict";
const result = document.querySelector("#result");
let snapshot, busy = false, allowlist = [], offset = 0, memoryQuery = "", more = false;
const page = document.body.dataset.page;
const text = (selector, value) => document.querySelectorAll(selector).forEach(e => {e.textContent = value ?? "未确定";});
async function api(action, payload) {
  const response = await fetch(`/api/${action}`, {method: payload === undefined ? "GET" : "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, ...(payload === undefined ? {} : {body: JSON.stringify(payload)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "请求失败；请从管理器重新打开地址。");
  return data;
}
function node(tag, value, className) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  if (className) element.className = className;
  return element;
}
function renderAllowlist() {
  const container = document.querySelector("#allowlist");
  if (!container) return;
  container.replaceChildren();
  allowlist.forEach(id => {
    const chip = node("span", id, "chip"), remove = node("button", "×");
    remove.type = "button"; remove.setAttribute("aria-label", `移除 ${id}`);
    remove.addEventListener("click", () => {allowlist = allowlist.filter(item => item !== id); renderAllowlist();});
    chip.append(remove); container.append(chip);
  });
  if (!allowlist.length) container.append(node("span", "名单为空；QQ 模式启动前需添加允许的好友。", "muted"));
}
function permissionMode() {
  const transport = document.querySelector('[data-config="SI_CHAT_TRANSPORT"]');
  if (transport) text("#permission-mode", transport.value === "qq" ? "allowlist（好友白名单）" : "disabled（none，无聊天连接）");
}
async function load() {
  snapshot = await api("state");
  document.querySelectorAll("[data-config]").forEach(e => {e.value = snapshot.values[e.dataset.config] || "";});
  document.querySelectorAll("[data-character]").forEach(e => {e.value = snapshot.character[e.dataset.character] || "";});
  document.querySelectorAll("[data-identity]").forEach(e => {e.textContent = snapshot.identity[e.dataset.identity] ?? "未确定";});
  document.querySelectorAll("[data-secret]").forEach(e => {e.value = ""; e.disabled = true;});
  document.querySelectorAll("[data-mode]").forEach(e => {e.value = "keep"; text(`#status-${e.dataset.mode}`, snapshot.secrets[e.dataset.mode] ? "（已配置）" : "（未配置）");});
  const yaml = document.querySelector("#yaml");
  if (yaml) yaml.value = snapshot.yaml;
  text("#identity", `角色：${snapshot.identity.working_name} · 开发代号：${snapshot.identity.development_id} · 内部 ID：${snapshot.identity.internal_id}`);
  text("#overrides", snapshot.overrides.length ? `这些环境变量优先于文件：${snapshot.overrides.join("、")}` : "当前未发现可编辑字段的环境变量覆盖。");
  allowlist = [...new Set((snapshot.values.SI_QQ_ALLOWED_USER_IDS || "").split(",").map(v => v.trim()).filter(Boolean))];
  renderAllowlist(); permissionMode();
}
function payload() {
  const values = {}, secrets = {};
  document.querySelectorAll("[data-config]").forEach(e => {values[e.dataset.config] = e.value;});
  if (page === "/chat") values.SI_QQ_ALLOWED_USER_IDS = allowlist.join(",");
  document.querySelectorAll("[data-mode]").forEach(e => {secrets[e.dataset.mode] = {mode: e.value, value: e.value === "replace" ? document.querySelector(`[data-secret="${e.dataset.mode}"]`).value : ""};});
  return {values, secrets, version: snapshot.version};
}
async function overview() {
  const data = await api("overview");
  document.querySelectorAll("[data-status]").forEach(e => {e.textContent = data.status[e.dataset.status] ?? "未知";});
  document.querySelectorAll("[data-overview]").forEach(e => {e.textContent = data[e.dataset.overview] ?? "暂无数据";});
}
const labels = {episodic:"经历", semantic:"语义", self:"自我", relationship:"关系", active:"有效", superseded:"已替代", archived:"已归档", explicit:"明确表达", observed:"观察", inferred:"推断", high:"高", medium:"中", low:"低"};
async function memories() {
  const data = await api(`memories?q=${encodeURIComponent(memoryQuery)}&offset=${offset}`);
  const list = document.querySelector("#memories");
  list.replaceChildren();
  data.items.forEach(item => {
    const card = node("article", undefined, "card");
    card.append(node("p", item.content, "memory-content"));
    const meta = node("div", undefined, "memory-meta");
    [item.memory_type, item.status, item.source].forEach(value => meta.append(node("span", labels[value] || value, "badge")));
    meta.append(node("span", `重要度：${labels[item.salience] || item.salience}`, "badge"));
    card.append(meta, node("p", `创建：${item.created_at}`, "muted"), node("p", item.embeddings.length ? "存在缓存（不代表当前 provider 索引有效）" : "无 embedding 缓存", "muted"));
    const details = node("details"); details.append(node("summary", "查看来源与证据"));
    details.append(node("p", `ID：${item.id}`), node("p", `最近回忆：${item.last_recalled_at || "未记录"}`));
    if (item.supersedes_memory_id) details.append(node("p", `替代的记忆：${item.supersedes_memory_id}`));
    const evidence = node("ul");
    item.evidence.forEach(e => evidence.append(node("li", `${e.evidence_kind} · ${e.evidence_ref}`)));
    if (!item.evidence.length) evidence.append(node("li", "没有证据引用"));
    details.append(evidence);
    item.embeddings.forEach(e => details.append(node("p", `${e.model_name} · ${e.dimensions} 维 · ${e.created_at}`)));
    card.append(details); list.append(card);
  });
  if (!data.items.length) list.append(node("section", "暂无匹配的记忆。此页面不会创建数据库或生成记忆。", "card empty"));
  more = data.more; text("#memory-note", data.message); text("#memory-page", `第 ${offset / 20 + 1} 页`);
}
function paging() {
  const previous = document.querySelector("#previous"), next = document.querySelector("#next");
  if (previous) previous.disabled = busy || offset === 0;
  if (next) next.disabled = busy || !more;
}
async function run(operation) {
  if (busy) return;
  busy = true;
  const buttons = [...document.querySelectorAll("button:not(:disabled)")];
  buttons.forEach(e => {e.disabled = true;});
  result.textContent = "处理中，请勿关闭管理器…";
  try {await operation();} catch (error) {result.textContent = error instanceof Error ? error.message : "操作失败";}
  finally {busy = false; buttons.forEach(e => {e.disabled = false;}); paging();}
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
  if (action === "logs") text("#logs", response.message);
  if (["start", "stop", "restart"].includes(action)) await overview();
  if (action === "qq") text('[data-overview="qq"]', response.message);
  result.textContent = response.message;
})));
document.querySelector("#reload")?.addEventListener("click", () => run(async () => {await load(); result.textContent = "已重新加载。";}));
document.querySelector("#refresh-status")?.addEventListener("click", () => run(async () => {await overview(); result.textContent = "状态已更新。";}));
document.querySelector("#menu")?.addEventListener("click", event => {const open = document.querySelector("#sidebar").classList.toggle("open"); event.currentTarget.setAttribute("aria-expanded", String(open));});
document.querySelector('[data-config="SI_CHAT_TRANSPORT"]')?.addEventListener("change", permissionMode);
document.querySelector("#add-qq")?.addEventListener("click", () => {
  const input = document.querySelector("#qq-id"), value = input.value.trim();
  if (!/^[1-9][0-9]*$/.test(value)) {result.textContent = "请输入有效的 QQ 号码。"; return;}
  if (!allowlist.includes(value)) allowlist.push(value);
  input.value = ""; renderAllowlist(); result.textContent = "已添加到本页草稿；请保存配置。";
});
document.querySelector("#memory-search")?.addEventListener("submit", event => {event.preventDefault(); run(async () => {memoryQuery = document.querySelector("#memory-query").value; offset = 0; await memories(); result.textContent = "搜索完成。";});});
document.querySelector("#previous")?.addEventListener("click", () => run(async () => {offset = Math.max(0, offset - 20); await memories(); result.textContent = "记忆已加载。";}));
document.querySelector("#next")?.addEventListener("click", () => run(async () => {offset += 20; await memories(); result.textContent = "记忆已加载。";}));
async function boot() {
  let token = location.hash.slice(1);
  history.replaceState(null, "", location.pathname);
  if (token) {
    const response = await fetch("/api/session", {method:"POST", credentials:"same-origin", headers:{Authorization:`Bearer ${token}`}});
    token = "";
    if (!response.ok) throw new Error("访问凭证无效；请从管理器重新打开地址。");
    if (document.body.dataset.login) {location.replace(location.pathname); return;}
  }
  if (document.body.dataset.login) {result.textContent = "请从 SI 管理器打开含临时凭证的完整地址。不要分享此地址。"; return;}
  await load();
  result.textContent = "已加载。已有密钥不会回显。";
  if (["/", "/chat", "/runtime", "/system"].includes(page)) await overview();
  if (page === "/memory") await memories();
}
run(boot);
