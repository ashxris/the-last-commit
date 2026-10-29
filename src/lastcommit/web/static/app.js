/* The Last Commit — frontend logic. Talks only to /api/*. No secrets here. */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var form = $("exhume-form");
  var urlInput = $("repo-url");
  var btn = $("exhume-btn");
  var quota = $("quota");
  var ouija = $("ouija");
  var stage = $("stage");
  var epitaph = $("epitaph");
  var reportCard = $("report-card");
  var reportEl = $("report");
  var reportRepo = $("report-repo");
  var downloadBtn = $("download-btn");

  var pollTimer = null;

  function setBusy(busy) {
    btn.disabled = busy;
    btn.textContent = busy ? "Exhuming…" : "Exhume";
    urlInput.disabled = busy;
  }

  function showEpitaph(message) {
    epitaph.textContent = message;
    epitaph.hidden = false;
  }

  function hideEpitaph() { epitaph.hidden = true; }

  function showQuota(remaining, limit) {
    quota.hidden = false;
    quota.innerHTML = "";
    var text = document.createElement("span");
    text.innerHTML = "";
    quota.textContent = "";
    var label = document.createTextNode("⸸ ");
    var strong = document.createElement("span");
    strong.className = "rest";
    strong.textContent = remaining + " of " + limit;
    quota.appendChild(label);
    quota.appendChild(strong);
    quota.appendChild(document.createTextNode(" exhumations left today"));
  }

  function renderMarkdown(markdown) {
    var raw = marked.parse(markdown, { headerIds: false, mangle: false, breaks: false });
    return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  }

  function showReport(jobId, repo, markdown) {
    ouija.hidden = true;
    reportRepo.textContent = repo || "the deceased";
    reportEl.innerHTML = renderMarkdown(markdown);
    downloadBtn.href = "/api/jobs/" + encodeURIComponent(jobId) + "/download";
    reportCard.hidden = false;
    reportCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function stopPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  function poll(jobId, attempts) {
    attempts = attempts || 0;
    stopPolling();
    pollTimer = setTimeout(function () {
      fetch("/api/jobs/" + encodeURIComponent(jobId))
        .then(function (res) {
          if (!res.ok) throw new Error("lost contact with the graveyard (HTTP " + res.status + ")");
          return res.json();
        })
        .then(function (job) {
          if (job.status === "done") {
            setBusy(false);
            return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/report")
              .then(function (res) { return res.json(); })
              .then(function (data) { showReport(jobId, data.repo, data.markdown); });
          }
          if (job.status === "failed") {
            setBusy(false);
            ouija.hidden = true;
            showEpitaph("The exhumation failed: " + (job.error || "an unspeakable error occurred."));
            return;
          }
          stage.textContent = job.stage || "Digging…";
          poll(jobId, attempts + 1);
        })
        .catch(function (err) {
          if (attempts < 5) { poll(jobId, attempts + 1); return; }
          setBusy(false);
          ouija.hidden = true;
          showEpitaph("Lost contact with the other side: " + err.message);
        });
    }, 1500);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    hideEpitaph();
    reportCard.hidden = true;
    stopPolling();

    var url = urlInput.value.trim();
    if (!url) { showEpitaph("Give the archaeologist a GitHub URL first."); return; }

    setBusy(true);
    ouija.hidden = false;
    stage.textContent = "Knocking on the crypt door…";

    fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url }),
    })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
      })
      .then(function (reply) {
        if (!reply.ok) {
          setBusy(false);
          ouija.hidden = true;
          showEpitaph(reply.data && reply.data.detail ? reply.data.detail : "The summoning was refused (HTTP " + reply.status + ").");
          return;
        }
        showQuota(reply.data.remaining_today, reply.data.per_day_limit);
        poll(reply.data.job_id);
      })
      .catch(function (err) {
        setBusy(false);
        ouija.hidden = true;
        showEpitaph("Could not reach the graveyard: " + err.message);
      });
  });
})();
