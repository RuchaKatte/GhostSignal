/* Ghost in the Logs - Frontend JS */

const API = "";

async function loadInvestigations() {
  try {
    const res = await fetch(`${API}/api/investigations`);
    const data = await res.json();
    renderInvestigations(data);
  } catch (err) {
    console.error("Failed to load investigations:", err);
  }
}

function renderInvestigations(investigations) {
  const list = document.getElementById("investigations-list");
  if (!list) return;

  if (!investigations || investigations.length === 0) {
    list.innerHTML = `
      <div class="empty-state">
        <span class="ghost-big">👻</span>
        <p>No incidents yet. The system rests...</p>
        <p class="hint">Run <code>make chaos SCENARIO=conn_leak</code> to wake the ghost.</p>
      </div>`;
    return;
  }

  list.innerHTML = investigations.map(inv => {
    const statusBadge = `badge-${inv.status === "done" ? "done" : inv.status === "running" ? "running" : inv.severity === "critical" ? "critical" : "pending"}`;
    const time = inv.alert_time ? new Date(inv.alert_time).toLocaleString() : new Date(inv.created_at).toLocaleString();
    return `
      <a href="/investigation/${inv.id}" class="investigation-card">
        <div class="inv-header">
          <span class="inv-title">${escHtml(inv.alertname)}</span>
          <span class="badge ${statusBadge}">${escHtml(inv.status)}</span>
        </div>
        <div class="inv-meta">
          Service: <strong>${escHtml(inv.service || "unknown")}</strong> &bull;
          ${escHtml(inv.summary || "")} &bull;
          ${time}
        </div>
      </a>`;
  }).join("");
}

function escHtml(s) {
  if (!s) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Poll for updates every 10s
loadInvestigations();
setInterval(loadInvestigations, 10000);
