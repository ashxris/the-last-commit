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
  var seance = $("seance");
  var chatLog = $("chat-log");
  var chatForm = $("chat-form");
  var chatInput = $("chat-input");
  var chatBtn = $("chat-btn");
  var chatQuota = $("chat-quota");

  var pollTimer = null;
  var currentJobId = null;

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
    currentJobId = jobId;
    ouija.hidden = true;
    reportRepo.textContent = repo || "the deceased";
    reportEl.innerHTML = renderMarkdown(markdown);
    downloadBtn.href = "/api/jobs/" + encodeURIComponent(jobId) + "/download";
    reportCard.hidden = false;
    seance.hidden = false;
    chatLog.innerHTML = "";
    reportCard.scrollIntoView({ behavior: "smooth", block: "start" });
    restoreChat(jobId);
  }

  /* ---------- the seance ---------- */

  function chatBubble(kind, markdown) {
    var div = document.createElement("div");
    div.className = "msg " + kind;
    if (markdown !== null && markdown !== undefined) {
      div.innerHTML = renderMarkdown(markdown);
    }
    chatLog.appendChild(div);
    div.scrollIntoView({ behavior: "smooth", block: "end" });
    return div;
  }

  function restoreChat(jobId) {
    fetch("/api/jobs/" + encodeURIComponent(jobId) + "/chat")
      .then(function (res) { return res.ok ? res.json() : { history: [] }; })
      .then(function (data) {
        (data.history || []).forEach(function (m) {
          chatBubble(m.role === "user" ? "you" : "ghost", m.content);
        });
      })
      .catch(function () { /* a dead transcript is not fatal */ });
  }

  function showChatQuota(remaining, limit) {
    chatQuota.hidden = false;
    chatQuota.textContent = "";
    chatQuota.appendChild(document.createTextNode("⸸ "));
    var strong = document.createElement("span");
    strong.className = "rest";
    strong.textContent = remaining + " of " + limit;
    chatQuota.appendChild(strong);
    chatQuota.appendChild(document.createTextNode(" whispers left today"));
  }

  chatForm.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!currentJobId) return;
    var question = chatInput.value.trim();
    if (!question) return;
    chatInput.value = "";
    chatBubble("you", question);

    var thinking = chatBubble("ghost", null);
    thinking.innerHTML = '<span class="thinking">the maintainer ponders…</span>';
    chatBtn.disabled = true;
    chatInput.disabled = true;

    fetch("/api/jobs/" + encodeURIComponent(currentJobId) + "/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
    })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
      })
      .then(function (reply) {
        thinking.remove();
        chatBtn.disabled = false;
        chatInput.disabled = false;
        chatInput.focus();
        if (!reply.ok) {
          chatBubble("ghost", "*" + (reply.data && reply.data.detail
            ? reply.data.detail
            : "the séance was refused (HTTP " + reply.status + ").") + "*");
          return;
        }
        chatBubble("ghost", reply.data.answer);
        showChatQuota(reply.data.remaining_today, reply.data.per_day_limit);
      })
      .catch(function (err) {
        thinking.remove();
        chatBtn.disabled = false;
        chatInput.disabled = false;
        chatBubble("ghost", "*The connection to the other side dropped: " + err.message + "*");
      });
  });

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
