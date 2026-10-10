// Review UI. Evidence text is untrusted, so the DOM is built with textContent only.
"use strict";

const SCENARIOS = ["success", "test-failure", "security-finding", "terraform-failure", "prompt-injection"];
let selectedBuild = null;

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") node.className = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ? JSON.stringify(body.detail) : response.statusText);
  return body;
}

function badge(text, tone) {
  return el("span", { class: "badge " + (tone || "") }, text);
}

function resultTone(result) {
  if (result === "SUCCESS" || result === "passed") return "ok";
  if (result === "FAILURE" || result === "failed") return "bad";
  return "warn";
}

function levelTone(level) {
  return { low: "ok", medium: "warn", high: "bad", critical: "bad", none: "ok" }[level] || "";
}

function refChip(id) {
  return el("span", { class: "ref", title: "Show evidence item", onclick: () => showEvidence(id) }, id);
}

function showEvidence(id) {
  const target = document.getElementById("evidence-" + id);
  if (!target) return;
  document.querySelectorAll("details.item.highlight").forEach((n) => n.classList.remove("highlight"));
  target.open = true;
  target.classList.add("highlight");
  target.scrollIntoView({ behavior: "smooth", block: "center" });
}

function fact(label, value) {
  return el("div", {}, el("dt", {}, label), el("dd", {}, value));
}

function list(items, render) {
  return el("ul", { class: "plain" }, items.map((item) => el("li", {}, render(item))));
}

// ---------------------------------------------------------------- panels

function pipelinePanel(evidence) {
  const build = evidence.build;
  const failed = evidence.stages.filter((s) => s.status === "failed").map((s) => s.name);
  const safeUrl = /^https?:\/\//i.test(build.url || "") ? build.url : null;
  const link = safeUrl ? el("a", { href: safeUrl, target: "_blank", rel: "noopener noreferrer" }, "Open in Jenkins") : "n/a";
  return el("section", { class: "panel" },
    el("h2", {}, "Pipeline overview ", badge("Jenkins: " + build.result, resultTone(build.result))),
    el("dl", { class: "facts" },
      fact("Build ID", build.id),
      fact("Scenario", build.scenario || "n/a"),
      fact("Started", build.started_at || "n/a"),
      fact("Duration", build.duration_seconds != null ? build.duration_seconds + " s" : "n/a"),
      fact("Failed stage", failed.length ? failed.join(", ") : "none"),
      fact("Source of truth", link)),
    el("div", { class: "stages" }, evidence.stages.map((stage) =>
      el("div", { class: "stage " + stage.status }, stage.name,
        el("small", {}, stage.status.replace("_", " ") +
          (stage.checks.length > 1 ? " · " + stage.checks.map((c) => c.id + ": " + c.status.replace("_", " ")).join(", ") : ""))))));
}

function analysisPanel(analysis) {
  const statusTone = { completed: "ok", inconclusive: "warn", unavailable: "bad" }[analysis.analysis_status];
  const panel = el("section", { class: "panel" },
    el("h2", {}, "AI investigation ", badge(analysis.analysis_status, statusTone)),
    el("dl", { class: "facts" },
      fact("Failure classification", analysis.failure_category.replaceAll("_", " ")),
      fact("Provider", analysis.provider.name + (analysis.provider.model ? " (" + analysis.provider.model + ")" : "")),
      fact("Analysis time", analysis.provider.duration_ms + " ms"),
      fact("Evidence grounding", analysis.grounding.status)),
    el("h3", {}, "Summary"), el("p", {}, analysis.summary));

  if (analysis.provider.fallback_used) {
    panel.append(el("div", { class: "notice warn" }, "Deterministic fallback used. " + (analysis.provider.fallback_reason || "")));
  }
  if (analysis.untrusted_content.length) {
    panel.append(el("div", { class: "notice warn" }, "Untrusted instruction-like text detected in evidence and ignored: ",
      analysis.untrusted_content.map((f) => refChip(f.evidence_reference))));
  }
  if (analysis.grounding.issues.length) {
    panel.append(el("h3", {}, "Grounding issues"), list(analysis.grounding.issues, (i) => i));
  }
  panel.append(el("h3", {}, "Observed facts (from evidence)"),
    analysis.observed_facts.length
      ? list(analysis.observed_facts, (f) => [f.fact, refChip(f.evidence_reference)])
      : el("p", { class: "empty" }, "None."));

  panel.append(el("h3", {}, "Possible root cause (hypothesis, not a fact)"));
  const hypothesis = analysis.root_cause_hypothesis;
  panel.append(hypothesis
    ? el("p", {}, hypothesis.description, " ", badge("confidence: " + hypothesis.confidence, ""),
        hypothesis.evidence_references.map(refChip))
    : el("p", { class: "empty" }, "No hypothesis offered."));

  if (analysis.risk_assessment) {
    const risk = analysis.risk_assessment;
    panel.append(el("h3", {}, "Risk"),
      el("p", {}, badge("estimated operational risk: " + risk.level, levelTone(risk.level)), " ",
        badge("tool-reported severity: " + risk.observed_severity, levelTone(risk.observed_severity))),
      el("p", {}, risk.reason));
  }
  if (analysis.missing_evidence.length) {
    panel.append(el("h3", {}, "Missing evidence"), list(analysis.missing_evidence, (m) => m));
  }
  if (analysis.limitations.length) {
    panel.append(el("h3", {}, "Limitations and uncertainty"), list(analysis.limitations, (l) => l));
  }
  return panel;
}

function reviewPanel(buildId, analysis, reviews) {
  const panel = el("section", { class: "panel" }, el("h2", {}, "Human review"),
    el("div", { class: "notice" },
      "Accept means you accept a recommendation for consideration or later manual implementation. " +
      "Recording a decision does not edit code, trigger Jenkins, deploy, change infrastructure or run any command."));
  if (!analysis.recommended_actions.length) {
    panel.append(el("p", { class: "empty" }, "No remediation is recommended for this build."));
    return panel;
  }
  const reviewer = el("input", { type: "text", id: "reviewer", placeholder: "Reviewer name (required)", maxlength: "80" });
  reviewer.value = localStorage.getItem("reviewer") || "";
  const comment = el("input", { type: "text", id: "comment", placeholder: "Comment (optional)", maxlength: "500" });
  panel.append(el("div", { class: "reviewer" }, reviewer, comment));
  const error = el("div", { class: "notice bad", hidden: "" });
  panel.append(error);

  const decide = async (recommendationId, decision) => {
    error.hidden = true;
    try {
      localStorage.setItem("reviewer", reviewer.value);
      await api(`/api/analyses/${encodeURIComponent(buildId)}/recommendations/${encodeURIComponent(recommendationId)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, reviewer: reviewer.value, comment: comment.value }),
      });
      await selectBuild(buildId);
    } catch (problem) {
      error.textContent = "Decision not recorded: " + problem.message;
      error.hidden = false;
    }
  };

  for (const action of analysis.recommended_actions) {
    const current = reviews[action.id];
    const status = current
      ? badge(`${current.decision}ed by ${current.reviewer} at ${current.created_at}`, current.decision === "accept" ? "ok" : "bad")
      : badge("awaiting human review", "warn");
    panel.append(el("div", { class: "rec" },
      el("p", {}, el("strong", {}, action.id + ": "), action.action),
      el("p", { class: "why" }, "Why: " + action.rationale, action.evidence_references.map(refChip)),
      el("div", { class: "controls" },
        el("button", { class: "act accept", onclick: () => decide(action.id, "accept") }, "Accept"),
        el("button", { class: "act reject", onclick: () => decide(action.id, "reject") }, "Reject"),
        status)));
  }
  return panel;
}

function evidencePanel(evidence) {
  return el("section", { class: "panel" }, el("h2", {}, "Evidence package (", evidence.items.length, " items, redacted)"),
    evidence.notes.length ? [el("h3", {}, "Packaging notes"), list(evidence.notes, (n) => n), el("h3", {}, "Items")] : null,
    evidence.items.map((item) => el("details", { class: "item", id: "evidence-" + item.id },
      el("summary", {}, el("span", { class: "ref" }, item.id), " ", item.title),
      el("pre", {}, JSON.stringify(item.attributes, null, 1) + (item.content ? "\n\n" + item.content : "")))));
}

function auditPanel(events) {
  const rows = events.map((e) => el("tr", {},
    el("td", {}, e.id), el("td", {}, e.created_at), el("td", {}, e.event_type),
    el("td", {}, e.recommendation_id || ""), el("td", {}, e.decision || ""),
    el("td", {}, e.reviewer || ""), el("td", { class: "wrap" }, e.comment || ""),
    el("td", {}, String(e.action_executed))));
  return el("section", { class: "panel" }, el("h2", {}, "Decision audit record"),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["#", "Time (UTC)", "Event", "Recommendation", "Decision", "Reviewer", "Comment", "Action executed"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))));
}

// ---------------------------------------------------------------- page

async function selectBuild(buildId) {
  selectedBuild = buildId;
  const [record, audit] = await Promise.all([
    api("/api/analyses/" + encodeURIComponent(buildId)),
    api("/api/audit?build_id=" + encodeURIComponent(buildId)),
  ]);
  document.getElementById("detail").replaceChildren(
    pipelinePanel(record.evidence),
    analysisPanel(record.analysis),
    reviewPanel(buildId, record.analysis, record.reviews),
    evidencePanel(record.evidence),
    auditPanel(audit));
  await refreshLists();
}

async function refreshLists() {
  const [builds, stats, ready] = await Promise.all([api("/api/analyses"), api("/api/stats"), api("/ready")]);
  document.getElementById("provider").textContent =
    "AI provider: " + ready.provider + (ready.model ? " (" + ready.model + ")" : "") + " · non-autonomous";
  document.getElementById("stats").textContent =
    `${stats.analyses} analyses · ${stats.review_events} review decisions recorded`;

  document.getElementById("builds").replaceChildren(...builds.map((b) =>
    el("li", {}, el("button", { class: "row" + (b.build_id === selectedBuild ? " selected" : ""), onclick: () => selectBuild(b.build_id) },
      "#" + b.number + " ", badge(b.jenkins_result, resultTone(b.jenkins_result)),
      el("span", { class: "sub" }, (b.scenario || "n/a") + " · " + b.failure_category.replaceAll("_", " "))))));

  document.getElementById("scenarios").replaceChildren(...SCENARIOS.map((name) => {
    const latest = builds.find((b) => b.scenario === name);
    const button = el("button", { class: "scenario" + (latest && latest.build_id === selectedBuild ? " selected" : "") },
      el("strong", {}, name), " ", latest ? badge(latest.jenkins_result, resultTone(latest.jenkins_result)) : null,
      el("span", { class: "sub" }, latest ? "build #" + latest.number + " · AI: " + latest.failure_category.replaceAll("_", " ") : "not run yet"));
    if (latest) button.addEventListener("click", () => selectBuild(latest.build_id));
    else button.disabled = true;
    return button;
  }));
  return builds;
}

(async function start() {
  const builds = await refreshLists();
  const wanted = new URLSearchParams(location.search).get("build");
  const first = builds.find((b) => b.build_id === wanted) || builds[0];
  if (first) await selectBuild(first.build_id);
})();
