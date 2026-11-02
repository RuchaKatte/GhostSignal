/* Ghost in the Logs - Frontend JS */

const API = "";

async function getJson(path) {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

async function loadInvestigations() {
  const list = document.getElementById("investigations-list");
  try {
    renderInvestigations(await getJson("/api/investigations"));
  } catch (err) {
    console.error("Failed to load investigations:", err);
    if (list) list.innerHTML = `<p class="load-error">Unable to load investigations.</p>`;
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
    const statusClass = {
      completed: "done",
      done: "done",
      partial: "done",
      running: "running",
      failed: "critical",
      pending: "pending"
    }[inv.status] || (inv.severity === "critical" ? "critical" : "pending");
    const statusBadge = `badge-${statusClass}`;
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
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatTime(value) {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? escHtml(value) : escHtml(date.toLocaleString());
}

function renderEvidenceIds(ids) {
  if (!Array.isArray(ids) || ids.length === 0) return "<span>None</span>";
  return ids.map(id => `<code class="evidence-id">${escHtml(id)}</code>`).join(" ");
}

function renderInvestigationDetail(investigation) {
  const container = document.getElementById("investigation-detail");
  if (!container) return;
  const result = investigation.investigation_result;
  const report = result?.report;
  const bundle = result?.bundle;
  if (!report || !bundle) {
    container.innerHTML = `
      <section class="detail-card">
        <h1>${escHtml(investigation.alertname || "Investigation")}</h1>
        <p>Status: <strong>${escHtml(investigation.status)}</strong></p>
        <p class="muted">The investigation report is not available yet.</p>
      </section>`;
    return;
  }

  const hypotheses = (report.root_cause_hypotheses || []).map(item => `
    <article class="claim-card">
      <div class="inv-header">
        <strong>${escHtml(item.description)}</strong>
        <span class="badge">${escHtml(item.status)}</span>
      </div>
      <p>Confidence: ${escHtml(item.confidence)}</p>
      <p>Supporting evidence: ${renderEvidenceIds(item.supporting_evidence_ids)}</p>
      <p>Contradicting evidence: ${renderEvidenceIds(item.contradicting_evidence_ids)}</p>
      ${item.rejected_evidence_ids?.length
        ? `<p class="load-error">Rejected references: ${renderEvidenceIds(item.rejected_evidence_ids)}</p>`
        : ""}
    </article>`).join("");
  const timeline = (report.timeline || []).map(item => `
    <li>
      <time>${formatTime(item.timestamp)}</time>
      <span>${escHtml(item.event || item.summary || item.message || item.metric_name || "Event")}</span>
      ${renderEvidenceIds(item.evidence_ids)}
    </li>`).join("");
  const logItems = (bundle.logs || []).map(log => `
    <li><time>${formatTime(log.timestamp)}</time> <code>${escHtml(log.service || "unknown")}</code>
      ${escHtml(log.message || log.line || "")} ${renderEvidenceIds([log.log_id])}</li>`).join("");
  const metricItems = (bundle.anomalies || []).map(metric => `
    <li><time>${formatTime(metric.timestamp)}</time> ${escHtml(metric.metric_name || "metric")}
      observed ${escHtml(metric.observed_value)}; expected ${escHtml(metric.expected_value)}
      ${renderEvidenceIds([metric.metric_id])}</li>`).join("");
  const nextSteps = (report.recommended_next_steps || []).map(step => `<li>${escHtml(step)}</li>`).join("");
  const stages = (report.completed_stages || []).map(stage =>
    `<span class="stage-chip">${escHtml(stage)}</span>`).join("");
  const warnings = (report.data_quality_warnings || []).map(warning =>
    `<li>${escHtml(warning)}</li>`).join("");

  container.innerHTML = `
    <section class="detail-card">
      <p><a class="back-link" href="/">← All investigations</a></p>
      <h1>${escHtml(investigation.alertname || bundle.alert_name)}</h1>
      <p class="inv-meta">${escHtml(investigation.service || bundle.service)} ·
        ${formatTime(investigation.alert_time)} ·
        <span class="badge">${escHtml(investigation.status)}</span></p>
      <h2>Investigation summary</h2>
      <p>${escHtml(report.incident_summary || investigation.summary || "No summary available.")}</p>
      ${stages ? `<p class="stage-list">Completed stages: ${stages}</p>` : ""}
      ${report.failed_stage ? `<p class="load-error">Pipeline stopped at: ${escHtml(report.failed_stage)}</p>` : ""}
      ${warnings ? `<h2>Data quality warnings</h2><ul class="evidence-list">${warnings}</ul>` : ""}
      ${nextSteps ? `<h2>Recommended next steps</h2><ul>${nextSteps}</ul>` : ""}
      <h2>Root-cause hypotheses</h2>
      ${hypotheses || `<p class="muted">No supported hypotheses were produced.</p>`}
      <h2>Timeline</h2>
      ${timeline ? `<ol class="timeline">${timeline}</ol>` : `<p class="muted">No timeline events.</p>`}
      <h2>Evidence</h2>
      <h3>Logs (${(bundle.logs || []).length})</h3>
      <ul class="evidence-list">${logItems || "<li>No log evidence.</li>"}</ul>
      <h3>Metric anomalies (${(bundle.anomalies || []).length})</h3>
      <ul class="evidence-list">${metricItems || "<li>No metric anomalies.</li>"}</ul>
    </section>`;
}

async function loadInvestigationDetail() {
  const container = document.getElementById("investigation-detail");
  const investigationId = window.location.pathname.split("/").filter(Boolean).pop();
  try {
    const investigation = await getJson(`/api/investigations/${encodeURIComponent(investigationId)}`);
    renderInvestigationDetail(investigation);
  } catch (err) {
    console.error("Failed to load investigation:", err);
    if (container) container.innerHTML = `<p class="load-error">Unable to load this investigation.</p>`;
  }
}

async function loadOfrenda() {
  const list = document.getElementById("ofrenda-list");
  if (!list) return;
  try {
    const entries = await getJson("/api/ofrenda");
    if (!entries.length) {
      list.innerHTML = `<div class="empty-state"><span class="ghost-big">🕯️</span>
        <p>No incidents have been laid to rest yet.</p></div>`;
      return;
    }
    list.innerHTML = entries.map(item => `
      <a class="investigation-card" href="/investigation/${encodeURIComponent(item.id)}">
        <div class="inv-header">
          <span class="inv-title">${escHtml(item.alertname)}</span>
          <span class="badge badge-done">${escHtml(item.service || "unknown")}</span>
        </div>
        <p>${escHtml(item.report?.incident_summary || item.summary || "")}</p>
        <p class="inv-meta">${formatTime(item.alert_time)} · ${escHtml(item.root_cause || "Cause undetermined")}</p>
      </a>`).join("");
  } catch (err) {
    console.error("Failed to load Ofrenda:", err);
    list.innerHTML = `<p class="load-error">Unable to load incident memories.</p>`;
  }
}

if (document.getElementById("investigations-list")) {
  loadInvestigations();
  setInterval(loadInvestigations, 10000);
} else if (document.getElementById("investigation-detail")) {
  loadInvestigationDetail();
} else if (document.getElementById("ofrenda-list")) {
  loadOfrenda();
}
