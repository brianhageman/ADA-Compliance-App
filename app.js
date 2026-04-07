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
let currentUploadFile = null;
let reviewState = [];

function setStatus(message) {
  statusEl.textContent = message;
}

function renderList(target, items, formatter) {
  target.innerHTML = "";
  const values = items && items.length ? items : ["None in this category."];
  values.forEach(function (item) {
    const li = document.createElement("li");
    li.textContent = formatter ? formatter(item) : item;
    target.appendChild(li);
  });
}

function updateAuditSummary(summary) {
  auditScoreEl.textContent = summary && summary.score ? summary.score : 0;
  auditAutoEl.textContent = summary && summary.auto_applied ? summary.auto_applied : 0;
  auditReviewEl.textContent = summary && summary.needs_review ? summary.needs_review : 0;
  auditManualEl.textContent = summary && summary.manual_checks ? summary.manual_checks : 0;
}

function statusLabel(status) {
  if (status === "approved") return "Approved";
  if (status === "deferred") return "Deferred";
  return "Needs review";
}

function mergeReviewState(incomingItems) {
  const priorById = new Map(reviewState.map(function (item) {
    return [item.review_id, item];
  }));
  return (incomingItems || []).map(function (item) {
    const prior = priorById.get(item.review_id);
    if (prior) {
      return Object.assign({}, item, {
        status: prior.status,
        suggested_value: prior.suggested_value,
      });
    }
    return Object.assign({}, item);
  });
}

function renderReviewItems(items) {
  reviewItemsEl.innerHTML = "";
  if (items.length === 0) {
    reviewItemsEl.innerHTML = '<p class="hint">No teacher-review items remain for this document.</p>';
    return;
  }

  items.forEach(function (item, index) {
    const card = document.createElement("article");
    card.className = "review-card";

    const meta = document.createElement("div");
    meta.className = "review-meta";
    meta.innerHTML =
      '<span class="pill">' + item.category + '</span>' +
      '<span class="pill muted">' + item.priority + ' priority</span>' +
      '<span class="pill muted">' + item.confidence + ' confidence</span>' +
      '<span class="pill status-' + item.status + '">' + statusLabel(item.status) + '</span>';

    const title = document.createElement("h4");
    title.textContent = item.title;

    const prompt = document.createElement("p");
    prompt.textContent = item.prompt + ' [' + item.location + ']';

    let editor;
    if (item.category === "heading_structure") {
      editor = document.createElement("select");
      editor.className = "review-select";
      ["Heading 1", "Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6"].forEach(function (optionLabel) {
        const option = document.createElement("option");
        option.value = optionLabel;
        option.textContent = optionLabel;
        if ((item.suggested_value || "Heading 2") === optionLabel) {
          option.selected = true;
        }
        editor.appendChild(option);
      });
      editor.addEventListener("change", function (event) {
        reviewState[index].suggested_value = event.target.value;
      });
    } else {
      editor = document.createElement("textarea");
      editor.value = item.suggested_value || "";
      editor.rows = 3;
      editor.addEventListener("input", function (event) {
        reviewState[index].suggested_value = event.target.value;
      });
    }

    const actions = document.createElement("div");
    actions.className = "review-actions";

    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "button secondary";
    approve.textContent = "Approve";
    approve.addEventListener("click", function () {
      reviewState[index].status = "approved";
      renderReviewItems(reviewState);
      syncSummaryFromReviewState();
    });

    const defer = document.createElement("button");
    defer.type = "button";
    defer.className = "button ghost";
    defer.textContent = "Defer";
    defer.addEventListener("click", function () {
      reviewState[index].status = "deferred";
      renderReviewItems(reviewState);
      syncSummaryFromReviewState();
    });

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "button ghost";
    reset.textContent = "Reset";
    reset.addEventListener("click", function () {
      reviewState[index].status = "needs_review";
      renderReviewItems(reviewState);
      syncSummaryFromReviewState();
    });

    actions.append(approve, defer, reset);
    card.append(meta, title, prompt, editor, actions);
    reviewItemsEl.append(card);
  });
}

function syncSummaryFromReviewState() {
  const needsReview = reviewState.filter(function (item) {
    return item.status === "needs_review";
  }).length;
  const summary = Object.assign({}, currentResult ? currentResult.auditSummary : {}, {
    needs_review: reviewState.length,
    manual_checks: needsReview,
  });
  currentResult.auditSummary = summary;
  updateAuditSummary(summary);
}

function showResults(payload) {
  currentResult = payload;
  reviewState = mergeReviewState(payload.reviewItems || []);

  resultTitle.textContent = payload.filename + ' (' + payload.documentType + ')';
  renderList(changesEl, payload.changes);
  renderList(issuesEl, payload.issues, function (issue) {
    return issue.category + ': ' + issue.message + ' [' + issue.location + ']';
  });
  renderList(limitationsEl, payload.limitations);
  renderReviewItems(reviewState);
  syncSummaryFromReviewState();

  if (payload.outputFileBase64) {
    downloadFileButton.classList.remove("hidden");
  } else {
    downloadFileButton.classList.add("hidden");
  }

  downloadReportButton.classList.remove("hidden");
  resultsEl.classList.remove("hidden");
}

function buildReportText() {
  if (currentResult === null) return "";

  const lines = [
    "ADA Compliance Bot Report",
    "",
    "Document: " + currentResult.filename,
    "Type: " + currentResult.documentType,
    "Audit score: " + (currentResult.auditSummary ? currentResult.auditSummary.score : 0),
    "Auto-applied fixes: " + (currentResult.auditSummary ? currentResult.auditSummary.auto_applied : 0),
    "Review items: " + reviewState.length,
    "Manual checks remaining: " + reviewState.filter(function (item) {
      return item.status === "needs_review";
    }).length,
    "",
    "Applied changes:",
  ];

  if (currentResult.changes && currentResult.changes.length) {
    currentResult.changes.forEach(function (entry) {
      lines.push("- " + entry);
    });
  } else {
    lines.push("- None");
  }

  lines.push("");
  lines.push("Issues found:");
  if (currentResult.issues && currentResult.issues.length) {
    currentResult.issues.forEach(function (issue) {
      lines.push("- " + issue.category + ": " + issue.message + " [" + issue.location + "]");
    });
  } else {
    lines.push("- None");
  }

  lines.push("");
  lines.push("Teacher review queue:");
  if (reviewState.length) {
    reviewState.forEach(function (item) {
      lines.push(
        "- " + item.title + " | " + item.status + " | " + item.location + " | Suggested value: " + (item.suggested_value || "n/a")
      );
    });
  } else {
    lines.push("- None");
  }

  lines.push("");
  lines.push("Current limitations:");
  if (currentResult.limitations && currentResult.limitations.length) {
    currentResult.limitations.forEach(function (item) {
      lines.push("- " + item);
    });
  } else {
    lines.push("- None");
  }

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

function hasApprovedServerChanges() {
  return reviewState.some(function (item) {
    return item.status === "approved" && item.review_id.indexOf("docx-") === 0;
  });
}

async function regenerateWithApprovedChanges() {
  if (currentUploadFile === null) {
    return currentResult;
  }

  const formData = new FormData();
  formData.append("document", currentUploadFile);
  formData.append("reviewState", JSON.stringify(reviewState));

  setStatus("Applying approved review changes to the document...");
  const payload = await fetchJson("/api/remediate", {
    method: "POST",
    body: formData,
  });
  showResults(payload);
  return payload;
}

async function downloadFixedFile() {
  let payload = currentResult;
  if (hasApprovedServerChanges()) {
    payload = await regenerateWithApprovedChanges();
  }
  const bytes = Uint8Array.from(atob(payload.outputFileBase64), function (char) {
    return char.charCodeAt(0);
  });
  downloadBlob(
    payload.outputFileName || "ada-remediated-file",
    payload.outputMimeType || "application/octet-stream",
    bytes
  );
}

function downloadReport() {
  const bytes = new TextEncoder().encode(buildReportText());
  const baseName = (currentResult && currentResult.filename ? currentResult.filename : "accessibility-report").replace(/\.[^.]+$/, "");
  const name = baseName + "-accessibility-report.txt";
  downloadBlob(name, "text/plain;charset=utf-8", bytes);
}

async function fetchJson(url, options) {
  const response = await fetch(url, options || {});
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

uploadForm.addEventListener("submit", async function (event) {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) {
    setStatus("Choose a file first.");
    return;
  }

  currentUploadFile = file;
  reviewState = [];

  const formData = new FormData();
  formData.append("document", file);
  setStatus("Uploading " + file.name + "...");

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

approveSafeButton.addEventListener("click", function () {
  reviewState = reviewState.map(function (item) {
    return item.suggested_value ? Object.assign({}, item, { status: "approved" }) : item;
  });
  renderReviewItems(reviewState);
  syncSummaryFromReviewState();
  setStatus("Suggested review text marked as approved.");
});

downloadReportButton.addEventListener("click", function () {
  downloadReport();
  setStatus("Accessibility report downloaded.");
});

downloadFileButton.addEventListener("click", async function () {
  try {
    await downloadFixedFile();
    setStatus("Remediated file downloaded.");
  } catch (error) {
    setStatus(error.message);
  }
});
