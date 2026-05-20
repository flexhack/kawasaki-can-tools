const grid = document.querySelector("#grid");
const charts = document.querySelector("#charts");
const canPill = document.querySelector("#can-pill");
const timeoutPill = document.querySelector("#timeout-pill");
const lastUpdate = document.querySelector("#last-update");
const dtcPanel = document.querySelector("#dtc-panel");
const ecuInfo = document.querySelector("#ecu-info");
const supportedPids = document.querySelector("#supported-pids");
const piHealth = document.querySelector("#pi-health");
const logStatus = document.querySelector("#log-status");
const rawFrames = document.querySelector("#raw-frames");

const chartKeys = ["rpm", "coolant_temp", "intake_manifold_pressure", "throttle_position", "o2_b1s1_voltage"];
const chartHistory = Object.fromEntries(chartKeys.map((key) => [key, []]));
let rawPaused = false;

function formatValue(value, digits) {
  if (value === null || value === undefined) return "--";
  return Number(value).toFixed(digits);
}

function statusClass(status) {
  if (status === "fresh") return "fresh";
  if (status === "stale" || status === "timeout" || status === "maybe_not_ready/open_loop") return "stale";
  return "disconnected";
}

function kv(target, rows) {
  target.innerHTML = rows.map(([key, value]) => `
    <div class="kv-row"><span>${key}</span><strong>${value ?? "--"}</strong></div>
  `).join("");
}

function renderCurrent(payload) {
  const { fields, order, meta } = payload;
  grid.innerHTML = "";
  for (const key of order) {
    const field = fields[key];
    const info = meta[key];
    const card = document.createElement("article");
    card.className = `card ${statusClass(field.status)}`;
    card.innerHTML = `
      <div class="label">${info.label}</div>
      <div class="value">${formatValue(field.value, info.digits)}</div>
      <div class="unit">${info.unit}</div>
      <div class="field-status">${field.status}</div>
    `;
    grid.appendChild(card);
  }
  renderCharts(fields, meta);
}

function renderCharts(fields, meta) {
  for (const key of chartKeys) {
    const value = fields[key]?.value;
    if (value !== null && value !== undefined) {
      chartHistory[key].push(Number(value));
      if (chartHistory[key].length > 60) chartHistory[key].shift();
    }
  }
  charts.innerHTML = chartKeys.map((key) => {
    const values = chartHistory[key];
    const max = Math.max(...values, 1);
    const bars = values.map((value) => `<i style="height:${Math.max(3, (value / max) * 56)}px"></i>`).join("");
    return `<article class="chart"><div>${meta[key].label}</div><div class="bars">${bars}</div></article>`;
  }).join("");
}

function renderStatus(status) {
  const online = status.can_status === "connected";
  canPill.textContent = online ? "CAN connected" : "CAN disconnected";
  canPill.className = `pill ${online ? "fresh" : "disconnected"}`;
  timeoutPill.textContent = `Timeouts ${status.pid_timeout_count ?? 0}`;
  lastUpdate.textContent = status.last_update ? `Last update ${status.last_update}` : status.message || "Waiting for data";
}

async function refreshPanels() {
  const [dtc, info, pids, health, logging] = await Promise.all([
    fetch("/api/dtc").then((r) => r.json()),
    fetch("/api/ecu-info").then((r) => r.json()),
    fetch("/api/supported-pids").then((r) => r.json()),
    fetch("/api/health").then((r) => r.json()),
    fetch("/api/log/status").then((r) => r.json()),
  ]);
  kv(dtcPanel, [["Stored", dtc.stored], ["Pending", dtc.pending], ["Updated", dtc.updated_at]]);
  kv(ecuInfo, [
    ["VIN", info.vin_masked],
    ["Calibration ID", info.calibration_id],
    ["CVN", info.cvn],
    ["CAN bitrate", info.can_bitrate],
    ["Request ID", info.request_id],
    ["Response ID", info.response_id],
  ]);
  supportedPids.innerHTML = (pids.decoded || []).map((pid) => `<div>${pid}</div>`).join("") || "<div>Waiting for supported PID query</div>";
  kv(piHealth, [
    ["Uptime", health.uptime_seconds ? `${Math.round(health.uptime_seconds)} s` : "--"],
    ["CPU temp", health.cpu_temp_c !== null ? `${health.cpu_temp_c} C` : "--"],
    ["Throttled", health.throttled],
    ["Disk free", `${health.disk_free_gb} GB`],
  ]);
  kv(logStatus, [["Active", logging.active ? "yes" : "no"], ["File", logging.file]]);
}

async function refreshRaw() {
  if (rawPaused) return;
  const payload = await fetch("/api/raw").then((r) => r.json());
  rawFrames.innerHTML = payload.frames.slice(-80).reverse().map((frame) => `
    <div class="raw-row"><span>${frame.time}</span><b>${frame.direction}</b><strong>${frame.id}</strong><code>${frame.data}</code></div>
  `).join("");
}

async function refresh() {
  try {
    const [current, status] = await Promise.all([
      fetch("/api/current").then((r) => r.json()),
      fetch("/api/status").then((r) => r.json()),
    ]);
    renderCurrent(current);
    renderStatus(status);
    await refreshRaw();
  } catch {
    canPill.textContent = "CAN disconnected";
    canPill.className = "pill disconnected";
    lastUpdate.textContent = "Dashboard connection lost";
  }
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab, .tab-panel").forEach((node) => node.classList.remove("active"));
    button.classList.add("active");
    document.querySelector(`#${button.dataset.tab}`).classList.add("active");
  });
});

document.querySelector("#refresh-dtc").addEventListener("click", async () => {
  await fetch("/api/dtc/refresh", { method: "POST" });
  refreshPanels();
});
document.querySelector("#start-log").addEventListener("click", async () => {
  await fetch("/api/log/start", { method: "POST" });
  refreshPanels();
});
document.querySelector("#stop-log").addEventListener("click", async () => {
  await fetch("/api/log/stop", { method: "POST" });
  refreshPanels();
});
document.querySelector("#pause-raw").addEventListener("click", (event) => {
  rawPaused = !rawPaused;
  event.target.textContent = rawPaused ? "Resume" : "Pause";
});
document.querySelector("#clear-raw").addEventListener("click", async () => {
  await fetch("/api/raw/clear", { method: "POST" });
  rawFrames.innerHTML = "";
});

refresh();
refreshPanels();
setInterval(refresh, 1000);
setInterval(refreshPanels, 5000);
