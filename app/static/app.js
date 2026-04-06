const uploadForm = document.getElementById("upload-form");
const fileInput = document.getElementById("document");
const searchForm = document.getElementById("search-form");
const driveQuery = document.getElementById("drive-query");
const driveFiles = document.getElementById("drive-files");
const sessionStatus = document.getElementById("session-status");
const teacherCard = document.getElementById("teacher-card");
const teacherPhoto = document.getElementById("teacher-photo");
const teacherName = document.getElementById("teacher-name");
const teacherEmail = document.getElementById("teacher-email");
const googleLogin = document.getElementById("google-login");
const logoutButton = document.getElementById("logout");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const resultTitle = document.getElementById("result-title");
const changesEl = document.getElementById("changes");
const issuesEl = document.getElementById("issues");
const limitationsEl = document.getElementById("limitations");
const downloadLink = document.getElementById("download-link");
const driveLink = document.getElementById("drive-link");
const reviewItemsEl = document.getElementById("review-items");
const auditScoreEl = document.getElementById("audit-score");
const auditAutoEl = document.getElementById("audit-auto");
const auditReviewEl = document.getElementById("audit-review");
const auditManualEl = document.getElementById("audit-manual");
const approveSafeButton = document.getElementById("approve-safe");
const downloadReportButton = document.getElementById("download-report");

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
  const values = items && items.length ? items : [];
  if (!values.length) {
    reviewItemsEl.innerHTML = "<p class=\"hint\">No teacher-review items remain for this document.</p>";
    return;
  }

  values.forEach((item, index) => {
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
    reviewItemsEl.appendChild(card);
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

  if (payload.downloadUrl) {
    downloadLink.href = payload.downloadUrl;
    downloadLink.classList.remove("hidden");
  } else {
    downloadLink.classList.add("hidden");
  }

  if (payload.driveCopy && payload.driveCopy.webViewLink) {
    driveLink.href = payload.driveCopy.webViewLink;
    driveLink.classList.remove("hidden");
  } else {
    driveLink.classList.add("hidden");
  }

  downloadReportButton.classList.remove("hidden");
  resultsEl.classList.remove("hidden");
}

function buildReportText() {
  if (!currentResult) return "";
  const lines = [
    `ADA Compliance Bot Report`,
    ``,
    `Document: ${currentResult.filename}`,
    `Type: ${currentResult.documentType}`,
    `Audit score: ${currentResult.auditSummary?.score ?? 0}`,
    `Auto-applied fixes: ${currentResult.auditSummary?.auto_applied ?? 0}`,
    `Review items: ${reviewState.length}`,
    `Manual checks remaining: ${reviewState.filter((item) => item.status === "needs_review").length}`,
    ``,
    `Applied changes:`,
    ...(currentResult.changes?.length ? currentResult.changes.map((entry) => `- ${entry}`) : ["- None"]),
    ``,
    `Issues found:`,
    ...(currentResult.issues?.length
      ? currentResult.issues.map((issue) => `- ${issue.category}: ${issue.message} [${issue.location}]`)
      : ["- None"]),
    ``,
    `Teacher review queue:`,
    ...(reviewState.length
      ? reviewState.map(
          (item) =>
            `- ${item.title} | ${item.status} | ${item.location} | Suggested value: ${item.suggested_value || "n/a"}`
        )
      : ["- None"]),
    ``,
    `Current limitations:`,
    ...(currentResult.limitations?.length ? currentResult.limitations.map((item) => `- ${item}`) : ["- None"]),
  ];
  return lines.join("\n");
}

function downloadReport() {
  const blob = new Blob([buildReportText()], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${(currentResult?.filename || "accessibility-report").replace(/\.[^.]+$/, "")}-accessibility-report.txt`;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

async function saveReportRecord() {
  if (!currentResult) return;
  try {
    await fetchJson("/api/review-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...currentResult,
        reviewItems: reviewState,
      }),
    });
  } catch (error) {
    setStatus(`Report downloaded, but server-side save failed: ${error.message}`);
  }
}

async function loadSession() {
  const payload = await fetchJson("/api/session");
  if (!payload.googleConfigured) {
    sessionStatus.textContent = "Google Workspace is not configured yet. Add OAuth credentials before Drive features will work.";
    googleLogin.classList.add("hidden");
    return;
  }

  if (payload.user) {
    sessionStatus.textContent = "Google Workspace connected.";
    teacherName.textContent = payload.user.name;
    teacherEmail.textContent = payload.user.email;
    teacherPhoto.src = payload.user.picture || "";
    teacherPhoto.alt = payload.user.name ? `${payload.user.name} profile photo` : "";
    teacherCard.classList.remove("hidden");
    googleLogin.classList.add("hidden");
    logoutButton.classList.remove("hidden");
    await loadDriveFiles();
    return;
  }

  sessionStatus.textContent = "Connect your Google account to browse Drive, Docs, and Slides.";
  googleLogin.classList.remove("hidden");
  logoutButton.classList.add("hidden");
  teacherCard.classList.add("hidden");
}

async function loadDriveFiles(query = "") {
  driveFiles.innerHTML = "<li>Loading Drive files...</li>";
  try {
    const payload = await fetchJson(`/api/drive/files?q=${encodeURIComponent(query)}`);
    driveFiles.innerHTML = "";

    if (!payload.files || payload.files.length === 0) {
      driveFiles.innerHTML = "<li>No matching Drive files found.</li>";
      return;
    }

    payload.files.forEach((file) => {
      const li = document.createElement("li");
      li.className = "file-row";

      const meta = document.createElement("div");
      const owner = file.owners && file.owners[0] ? file.owners[0].displayName : "Unknown owner";
      meta.innerHTML = `
        <strong>${file.name}</strong>
        <span>${file.mimeType}</span>
        <small>${owner}</small>
      `;

      const actions = document.createElement("div");
      actions.className = "file-actions";

      const remediate = document.createElement("button");
      remediate.className = "button secondary";
      remediate.type = "button";
      remediate.textContent = "Audit and remediate";
      remediate.addEventListener("click", async () => {
        setStatus(`Remediating ${file.name} from Drive...`);
        try {
          const payload = await fetchJson("/api/remediate-drive", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ fileId: file.id }),
          });
          showResults(payload);
          setStatus(
            payload.supported
              ? "Remediation finished. Review the teacher approval queue before sharing."
              : "This file type is not auto-remediated yet. Review the limits shown below."
          );
        } catch (error) {
          setStatus(error.message);
        }
      });

      const open = document.createElement("a");
      open.className = "button ghost";
      open.href = file.webViewLink || "#";
      open.target = "_blank";
      open.rel = "noreferrer";
      open.textContent = "Open";

      actions.append(remediate, open);
      li.append(meta, actions);
      driveFiles.appendChild(li);
    });
  } catch (error) {
    driveFiles.innerHTML = `<li>${error.message}</li>`;
  }
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
    const payload = await fetchJson("/api/remediate-upload", {
      method: "POST",
      body: formData,
    });
    showResults(payload);
    setStatus(
      payload.supported
        ? "Audit complete. Review the teacher approval queue before sharing the accessible copy."
        : "This file type is not auto-remediated yet. Review the limits shown below."
    );
  } catch (error) {
    setStatus(error.message);
  }
});

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await loadDriveFiles(driveQuery.value);
});

logoutButton.addEventListener("click", async () => {
  await fetchJson("/api/logout", { method: "POST" });
  driveFiles.innerHTML = "";
  await loadSession();
  setStatus("Signed out.");
});

approveSafeButton.addEventListener("click", () => {
  reviewState = reviewState.map((item) =>
    item.suggested_value ? { ...item, status: "approved" } : item
  );
  renderReviewItems(reviewState);
  syncSummaryFromReviewState();
  setStatus("Suggested review text marked as approved. Export the report if you want a checklist record.");
});

downloadReportButton.addEventListener("click", async () => {
  downloadReport();
  await saveReportRecord();
  setStatus("Accessibility report downloaded.");
});

loadSession().catch((error) => {
  setStatus(error.message);
});

const params = new URLSearchParams(window.location.search);
if (params.get("authError")) {
  setStatus(`Google sign-in error: ${params.get("authError")}`);
}
