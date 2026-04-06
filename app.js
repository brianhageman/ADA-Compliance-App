const uploadForm = document.getElementById("upload-form");
const fileInput = document.getElementById("document");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const resultTitle = document.getElementById("result-title");
const changesEl = document.getElementById("changes");
const issuesEl = document.getElementById("issues");
const limitationsEl = document.getElementById("limitations");
const reviewItemsEl = document.getElementById("review-items");
const auditScoreEl = document.getElementById("audit-score");
const auditAutoEl = document.getElementById("audit-auto");
const auditReviewEl = document.getElementById("audit-review");
const auditManualEl = document.getElementById("audit-manual");
const approveSafeButton = document.getElementById("approve-safe");
const downloadReportButton = document.getElementById("download-report");
const downloadFileButton = document.getElementById("download-file");

let currentResult = null;
let reviewState = [];

function setStatus(message) {
  statusEl.textContent = message;
}

function renderList(target, items, formatter) {
  target.innerHTML = "";
  const values = items && items.length ? items : ["None in this category."];
  values.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = formatter ? formatter(item) : item;
    target.appendChild(li);
  });
}

function updateAuditSummary(summary) {
  auditScoreEl.textContent = summary?.score ?? 0;
  auditAutoEl.textContent = summary?.auto_applied ?? 0;
  auditReviewEl.textContent = summary?.needs_review ?? 0;
  auditManualEl.textContent = summary?.manual_checks ?? 0;
}

function statusLabel(status) {
  if (status === "approved") return "Approved";
  if (status === "deferred") return "Deferred";
  return "Needs review";
}

function renderReviewItems(items) {
  reviewItemsEl.innerHTML = "";
  if (!items.length) {
    reviewItemsEl.innerHTML = '<p class="hint">No teacher-review items remain for this document.</p>';
    return;
  }

  items.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "review-card";

    const meta = document.createElement("div");
    meta.className = "review-meta";
    meta.innerHTML = `
      <span class="pill">${item.category}</span>
      <span class="pill muted">${item.priority} priority</span>
      <span class="pill muted">${item.confidence} confidence</span>
      <span class="pill status-${item.status}">${statusLabel(item.status)}</span>
    `;

    const title = document.createElement("h4");
    title.textContent = item.title;

    const prompt = document.createElement("p");
    prompt.textContent = `${item.prompt} [${item.location}]`;

    const textarea = document.createElement("textarea");
    textarea.value = item.suggested_value || "";
    textarea.rows = 3;
    textarea.addEventListener("input", (event) => {
      reviewState[index].suggested_value = event.target.value;
    });

    const actions = document.createElement("div");
    actions.className = "review-actions";

    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "button secondary";
    approve.textContent = "Approve";
    approve.addEventListener("click", () => {
      reviewState[index].status = "approved";
      renderReviewItems(reviewState);
      syncSummaryFromReviewState();
    });

    const defer = document.createElement("button");
    defer.type = "button";
    defer.className = "button ghost";
    defer.textContent = "Defer";
    defer.addEventListener("click", () => {
      reviewState[index].status = "deferred";
      renderReviewItems(reviewState);
      syncSummaryFromReviewState();
    });

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "button ghost";
    reset.textContent = "Reset";
    reset.addEventListener("click", () => {
      reviewState[index].status = "needs_review";
      renderReviewItems(reviewState);
      syncSummaryFromReviewState();
    });

    actions.append(approve, defer, reset);
    card.append(meta, title, prompt, textarea, actions);
    reviewItemsEl.append(card);
  });
}

function syncSummaryFromReviewState() {
  const needsReview = reviewState.filter((item) => item.status === "needs_review").length;
  const summary = {
    ...(currentResult?.auditSummary || {}),
    needs_review: reviewState.length,
    manual_checks: needsReview,
  };
  currentResult.auditSummary = summary;
  updateAuditSummary(summary);
}

function showResults(payload) {
  currentResult = payload;
  reviewState = (payload.reviewItems || []).map((item) => ({ ...item }));

  resultTitle.textContent = `${payload.filename} (${payload.documentType})`;
  renderList(changesEl, payload.changes);
  renderList(issuesEl, payload.issues, (issue) => `${issue.category}: ${issue.message} [${issue.location}]`);
  renderList(limitationsEl, payload.limitations);
  renderReviewItems(reviewState);
  updateAuditSummary(payload.auditSummary);

  if (payload.outputFileBase64) {
    downloadFileButton.classList.remove("hidden");
  } else {
    downloadFileButton.classList.add("hidden");
  }

  downloadReportButton.classList.remove("hidden");
  resultsEl.classList.remove("hidden");
}

function buildReportText() {
  if (!currentResult) return "";
  const lines = [
    "ADA Compliance Bot Report",
    "",
    `Document: ${currentResult.filename}`,
    `Type: ${currentResult.documentType}`,
    `Audit score: ${currentResult.auditSummary?.score ?? 0}`,
    `Auto-applied fixes: ${currentResult.auditSummary?.auto_applied ?? 0}`,
    `Review items: ${reviewState.length}`,
    `Manual checks remaining: ${reviewState.filter((item) => item.status === "needs_review").length}`,
    "",
    "Applied changes:",
    ...(currentResult.changes?.length ? currentResult.changes.map((entry) => `- ${entry}`) : ["- None"]),
    "",
    "Issues found:",
    ...(currentResult.issues?.length
      ? currentResult.issues.map((issue) => `- ${issue.category}: ${issue.message} [${issue.location}]`)
      : ["- None"]),
    "",
    "Teacher review queue:",
    ...(reviewState.length
      ? reviewState.map(
          (item) =>
            `- ${item.title} | ${item.status} | ${item.location} | Suggested value: ${item.suggested_value || "n/a"}`
        )
      : ["- None"]),
    "",
    "Current limitations:",
    ...(currentResult.limitations?.length ? currentResult.limitations.map((item) => `- ${item}`) : ["- None"]),
  ];
  return lines.join("\n");
}

function downloadBlob(filename, mimeType, contentBytes) {
  const blob = new Blob([contentBytes], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function downloadFixedFile() {
  const bytes = Uint8Array.from(atob(currentResult.outputFileBase64), (char) => char.charCodeAt(0));
  downloadBlob(
    currentResult.outputFileName || "ada-remediated-file",
    currentResult.outputMimeType || "application/octet-stream",
    bytes
  );
}

function downloadReport() {
  const bytes = new TextEncoder().encode(buildReportText());
  const name = `${(currentResult?.filename || "accessibility-report").replace(/\.[^.]+$/, "")}-accessibility-report.txt`;
  downloadBlob(name, "text/plain;charset=utf-8", bytes);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) {
    setStatus("Choose a file first.");
    return;
  }

  const formData = new FormData();
  formData.append("document", file);
  setStatus(`Uploading ${file.name}...`);

  try {
    const payload = await fetchJson("/api/remediate", {
      method: "POST",
      body: formData,
    });
    showResults(payload);
    setStatus(
      payload.supported
        ? "Audit complete. Review the teacher approval queue, then download the fixed file."
        : "This file type is not auto-remediated yet. Review the limits shown below."
    );
  } catch (error) {
    setStatus(error.message);
  }
});

approveSafeButton.addEventListener("click", () => {
  reviewState = reviewState.map((item) =>
    item.suggested_value ? { ...item, status: "approved" } : item
  );
  renderReviewItems(reviewState);
  syncSummaryFromReviewState();
  setStatus("Suggested review text marked as approved.");
});

downloadReportButton.addEventListener("click", () => {
  downloadReport();
  setStatus("Accessibility report downloaded.");
});

downloadFileButton.addEventListener("click", () => {
  downloadFixedFile();
  setStatus("Remediated file downloaded.");
});
