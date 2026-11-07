"use strict";

(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  const STATUS = {
    SUCCEEDED: { key: "pass", label: "passed" },
    FAILED: { key: "fail", label: "failed" },
    TIMED_OUT: { key: "timeout", label: "timed out" },
    CANCELLED: { key: "cancelled", label: "cancelled" },
    BLOCKED: { key: "skipped", label: "skipped, dependency did not pass" },
    QUEUED: { key: "running", label: "queued" },
    RUNNING: { key: "running", label: "running" },
  };

  const CLASSIFICATION = {
    RETRY_FLAKY: { key: "flaky", label: "retry", title: "Failed, then passed on retry within one run" },
    SAME_COMMIT_VARIABILITY: { key: "flaky", label: "same commit", title: "Passed and failed in separate runs of one clean commit" },
    MIXED_UNATTRIBUTED: { key: "neutral", label: "mixed", title: "Passed and failed on different commits; may be a real regression" },
  };

  const state = {
    overview: null,
    insights: null,
    predictions: null,
    repositoryId: null,
    pipeline: null,
    version: null,
  };

  const $ = (id) => document.getElementById(id);

  /* ---------- DOM helpers (text only, never HTML strings) ---------- */

  function el(tag, attributes = {}, children = []) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "text") node.textContent = value;
      else if (name === "class") node.className = value;
      else node.setAttribute(name, value === true ? "" : String(value));
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function svg(tag, attributes = {}, children = []) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "text") node.textContent = value;
      else node.setAttribute(name, String(value));
    }
    for (const child of [].concat(children)) {
      if (child) node.append(child);
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.firstChild.remove();
    return node;
  }

  /* ---------- Formatting ---------- */

  const shortSha = (sha) => (sha ? sha.slice(0, 7) : "-");

  function percent(value, digits = 0) {
    if (value === null || value === undefined) return "-";
    return `${(value * 100).toFixed(digits)}%`;
  }

  function seconds(value) {
    if (value === null || value === undefined) return "-";
    if (value < 1) return `${(value * 1000).toFixed(1)} ms`;
    if (value < 120) return `${value.toFixed(1)} s`;
    return `${Math.floor(value / 60)}m ${String(Math.round(value % 60)).padStart(2, "0")}s`;
  }

  function timestamp(value) {
    if (!value) return "-";
    const date = new Date(value);
    return date.toISOString().replace("T", " ").slice(0, 16) + "Z";
  }

  const status = (value) => STATUS[value] || { key: "skipped", label: String(value).toLowerCase() };

  /* ---------- Tooltip ---------- */

  const tooltip = $("tooltip");
  let tipOwner = null;

  function showTip(owner, content, anchor) {
    tipOwner = owner;
    clear(tooltip);
    tooltip.append(el("div", { class: "tip-title", text: content.title }));
    if (content.rows && content.rows.length) {
      const list = el("dl");
      for (const [term, detail] of content.rows) {
        list.append(el("dt", { text: term }), el("dd", { text: detail }));
      }
      tooltip.append(list);
    }
    tooltip.hidden = false;
    const box = anchor || owner.getBoundingClientRect();
    const tip = tooltip.getBoundingClientRect();
    let left = box.left + box.width / 2 - tip.width / 2;
    let top = box.top - tip.height - 8;
    if (top < 48) top = box.bottom + 8;
    left = Math.max(8, Math.min(left, window.innerWidth - tip.width - 8));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function hideTip(owner) {
    if (owner && owner !== tipOwner) return;
    tooltip.hidden = true;
    tipOwner = null;
  }

  function tip(node, content, label) {
    node.setAttribute("tabindex", "0");
    node.setAttribute("aria-label", label || content().title);
    if (!node.getAttribute("role")) node.setAttribute("role", "img");
    node.addEventListener("mouseenter", () => showTip(node, content()));
    node.addEventListener("mouseleave", () => hideTip(node));
    node.addEventListener("focus", () => showTip(node, content()));
    node.addEventListener("blur", () => hideTip(node));
    return node;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideTip();
  });
  window.addEventListener("scroll", () => hideTip(), { passive: true });

  /* ---------- Empty states ---------- */

  function empty(container, title, ...paragraphs) {
    const box = el("div", { class: "empty" }, [el("strong", { text: title })]);
    for (const parts of paragraphs) {
      const paragraph = el("p");
      for (const part of [].concat(parts)) {
        paragraph.append(typeof part === "string" ? part : el("code", { text: part.code }));
      }
      box.append(paragraph);
    }
    clear(container).append(box);
  }

  function noSelection(container) {
    if (state.overview && !state.overview.repositories.length) {
      empty(
        container,
        "No repositories registered",
        ["Register one with ", { code: "POST /repositories" }, " and save a pipeline, or load sample history with ", { code: "python -m buildscope.demo seed --database data/demo.db" }, "."],
      );
    } else if (state.overview && !state.overview.pipelines.length) {
      empty(
        container,
        "No pipelines saved for this repository",
        ["Save a pipeline YAML with ", { code: `POST /repositories/${state.repositoryId}/pipelines` }, "."],
      );
    } else {
      empty(container, "No pipeline selected", "Choose a pipeline version in the left rail.");
    }
  }

  /* ---------- Data loading ---------- */

  async function getJSON(path, params = {}) {
    const url = new URL(path, window.location.origin);
    for (const [name, value] of Object.entries(params)) {
      if (value !== null && value !== undefined) url.searchParams.set(name, value);
    }
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!response.ok) {
      const detail = body && typeof body.detail === "string" ? body.detail : response.statusText;
      const error = new Error(`${response.status} ${detail}`);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function readHash() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    return {
      repository: params.get("repository"),
      pipeline: params.get("pipeline"),
      version: params.get("version") ? Number(params.get("version")) : null,
    };
  }

  function writeHash() {
    const params = new URLSearchParams();
    if (state.repositoryId) params.set("repository", state.repositoryId);
    if (state.pipeline) params.set("pipeline", state.pipeline);
    if (state.version) params.set("version", String(state.version));
    history.replaceState(null, "", `#${params}`);
  }

  function notice(message) {
    const node = $("notice");
    node.hidden = !message;
    node.textContent = message || "";
  }

  async function loadOverview(repositoryId) {
    notice(null);
    try {
      state.overview = await getJSON("/dashboard/api/overview", { repository_id: repositoryId });
    } catch (error) {
      notice(`Could not load repositories: ${error.message}`);
      return;
    }
    state.repositoryId = state.overview.selected_repository_id;
    const pipelines = state.overview.pipelines;
    const wanted = readHash();
    const match = pipelines.find(
      (item) => item.name === (state.pipeline || wanted.pipeline) && item.version === (state.version || wanted.version),
    );
    const fallback = pipelines[0];
    state.pipeline = (match || fallback || {}).name || null;
    state.version = (match || fallback || {}).version || null;
    renderRail();
    await loadInsights();
  }

  async function loadInsights() {
    renderCrumbs();
    writeHash();
    state.insights = null;
    state.predictions = null;

    if (!state.repositoryId || !state.pipeline) {
      renderAll();
      return;
    }

    const params = {
      repository_id: state.repositoryId,
      pipeline_name: state.pipeline,
      version: state.version,
    };

    const [insights, predictions] = await Promise.allSettled([
      getJSON("/dashboard/api/insights", params),
      getJSON("/dashboard/api/predictions", params),
    ]);

    if (insights.status === "fulfilled") state.insights = insights.value;
    else notice(`Could not load pipeline insights: ${insights.reason.message}`);

    state.predictions =
      predictions.status === "fulfilled"
        ? predictions.value
        : { enabled: false, error: predictions.reason.message };

    $("loaded-at").textContent = `loaded ${new Date().toTimeString().slice(0, 8)}`;
    renderAll();
  }

  /* ---------- Rail and breadcrumbs ---------- */

  function statusDot(value) {
    const info = value ? status(value) : { key: "neutral", label: "no runs" };
    return el("span", { class: `dot bg-${info.key}`, title: info.label, "aria-hidden": "true" });
  }

  function renderRail() {
    const repositories = clear($("repositories"));
    const overview = state.overview;

    if (!overview.repositories.length) {
      repositories.append(el("li", { class: "rail-group", text: "None registered" }));
    }

    for (const repository of overview.repositories) {
      const button = el("button", {
        type: "button",
        class: "rail-item",
        "aria-current": repository.id === state.repositoryId ? "true" : "false",
      }, [el("span", { class: "grow", text: repository.name })]);
      button.addEventListener("click", () => {
        state.pipeline = null;
        state.version = null;
        loadOverview(repository.id);
      });
      repositories.append(el("li", {}, [button]));
    }

    const list = clear($("pipelines"));
    const byName = new Map();
    for (const item of overview.pipelines) {
      if (!byName.has(item.name)) byName.set(item.name, []);
      byName.get(item.name).push(item);
    }

    if (!byName.size) {
      list.append(el("li", { class: "rail-group", text: "No pipelines saved" }));
    }

    for (const [name, versions] of byName) {
      list.append(el("li", { class: "rail-group", text: name }));
      for (const item of versions) {
        const current = item.name === state.pipeline && item.version === state.version;
        const button = el("button", {
          type: "button",
          class: "rail-item version",
          "aria-current": current ? "true" : "false",
          "aria-label": `${name} version ${item.version}, ${item.runs} runs, last ${item.last_status ? status(item.last_status).label : "never run"}`,
        }, [
          statusDot(item.last_status),
          el("span", { class: "grow mono", text: `v${item.version}` }),
          el("span", { class: "num", text: String(item.runs) }),
        ]);
        button.addEventListener("click", () => {
          state.pipeline = item.name;
          state.version = item.version;
          renderRail();
          loadInsights();
        });
        list.append(el("li", {}, [button]));
      }
    }
  }

  function renderCrumbs() {
    const crumbs = clear($("crumbs"));
    const repository = state.overview && state.overview.repositories.find((item) => item.id === state.repositoryId);
    if (repository) crumbs.append(el("span", { text: repository.name }));
    if (state.pipeline) crumbs.append(el("span", { text: state.pipeline }));
    if (state.version) crumbs.append(el("span", { text: `v${state.version}` }));
  }

  /* ---------- Rendering ---------- */

  function renderAll() {
    hideTip();
    renderSummary();
    renderMatrix();
    renderDag();
    renderRisk();
    renderTimeline();
    renderFlaky();
    renderBenchmarks();
  }

  function stat(label, value, detail, title) {
    return el("div", { class: "stat", title }, [
      el("div", { class: "stat-label", text: label }),
      el("div", { class: "stat-value", text: value }),
      el("div", { class: "stat-detail", text: detail }),
    ]);
  }

  function renderSummary() {
    const node = clear($("summary"));
    const insights = state.insights;

    if (!insights) {
      const reason = state.overview && !state.overview.repositories.length
        ? "register a repository"
        : "select a pipeline version";
      node.append(
        stat("Runs", "-", reason),
        stat("Pass rate", "-", ""),
        stat("Open regressions", "-", ""),
        stat("Flaky tests", "-", ""),
        stat("Worst-trending stage", "-", ""),
      );
      return;
    }

    const summary = insights.summary;
    const mixed = insights.flaky_tests.length - summary.flaky_tests;
    const worst = summary.worst_stage;

    node.append(
      stat("Runs", String(summary.runs), `v${insights.version}, last ${summary.window_runs} max`),
      stat("Pass rate", percent(summary.pass_rate, 1), "finished runs"),
      stat("Open regressions", String(summary.open_regressions), `${insights.regressions.length} windows seen`),
      stat("Flaky tests", String(summary.flaky_tests), mixed > 0 ? `+${mixed} mixed · ${insights.test_identities} tracked` : `${insights.test_identities} tracked`),
      worst
        ? stat(
          "Worst-trending stage",
          worst.stage,
          `fail ${percent(worst.previous_failure_rate)} → ${percent(worst.recent_failure_rate)} over last ${worst.window_runs} runs`,
        )
        : stat("Worst-trending stage", "none", "no stage failing more often than before"),
    );
  }

  /* Run matrix: the signature view. */

  function statusMark(group, value, x, y, width, height) {
    const info = status(value);
    group.append(svg("rect", {
      x, y, width, height, rx: 2,
      class: `fill-${info.key}`,
    }));
    const cx = x + width / 2;
    const cy = y + height / 2;
    const reach = Math.min(width, height) / 2 - 3;
    if (reach < 2) return;
    if (info.key === "fail") {
      group.append(svg("path", {
        class: "glyph",
        d: `M${cx - reach} ${cy - reach}L${cx + reach} ${cy + reach}M${cx + reach} ${cy - reach}L${cx - reach} ${cy + reach}`,
      }));
    } else if (info.key === "timeout") {
      group.append(svg("rect", { x, y, width, height, rx: 2, fill: "url(#hatch)" }));
    } else if (info.key === "cancelled") {
      group.append(svg("path", { class: "glyph", d: `M${cx - reach} ${cy}L${cx + reach} ${cy}` }));
    }
  }

  function legendSwatch(value, retried) {
    const swatch = svg("svg", { width: 14, height: 12, "aria-hidden": "true" });
    if (value) statusMark(swatch, value, 0.5, 0.5, 13, 11);
    if (retried) {
      swatch.append(svg("rect", { x: 0.5, y: 0.5, width: 13, height: 11, rx: 2, class: "fill-pass" }));
      swatch.append(svg("path", { class: "retry-mark", d: "M8 0.5L13.5 0.5L13.5 6Z" }));
    }
    return swatch;
  }

  function hatchPattern() {
    return svg("defs", {}, [
      svg("pattern", { id: "hatch", width: 4, height: 4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, [
        svg("line", { x1: 0, y1: 0, x2: 0, y2: 4, stroke: "rgba(255,255,255,0.7)", "stroke-width": 1.5 }),
      ]),
    ]);
  }

  function renderMatrix() {
    const container = clear($("matrix"));
    const legend = clear($("matrix-legend"));
    const insights = state.insights;
    $("matrix-meta").textContent = "";

    if (!insights) {
      noSelection(container);
      return;
    }

    const { columns, rows } = insights.matrix;
    if (!columns.length) {
      empty(
        container,
        "No runs recorded for this pipeline version",
        ["Approve and enqueue a run, then start a worker: ",
          { code: `python -m buildscope.workers.cli enqueue --repository ${state.repositoryId} --pipeline ${state.pipeline} --version ${state.version} --approve` }],
        ["Or explore sample data with ", { code: "python -m buildscope.demo seed --database data/demo.db" }, "."],
      );
      return;
    }

    $("matrix-meta").textContent = `last ${columns.length} runs, oldest left`;

    const labelWidth = 156;
    const available = Math.max(320, container.clientWidth - 24);
    const pitch = Math.max(16, Math.min(30, Math.floor((available - labelWidth) / columns.length)));
    const cellWidth = pitch - 2;
    const cellHeight = 20;
    const bandHeight = 6;
    const top = bandHeight + 6;
    const height = top + rows.length * (cellHeight + 2) + 28;
    const width = labelWidth + columns.length * pitch;

    const chart = svg("svg", {
      width, height,
      role: "grid",
      "aria-label": `Run matrix: ${rows.length} stages by ${columns.length} runs. Use arrow keys to move between cells.`,
    });
    chart.append(hatchPattern());

    // Alternate background bands group consecutive runs of the same commit.
    let group = 0;
    let previous = null;
    const groupStart = [];
    columns.forEach((column, index) => {
      if (column.commit !== previous) {
        group += 1;
        groupStart.push(index);
      }
      previous = column.commit;
      if (group % 2 === 0) {
        chart.append(svg("rect", {
          class: "band-alt",
          x: labelWidth + index * pitch - 1,
          y: 0,
          width: pitch,
          height: height - 22,
        }));
      }
    });

    // Run outcome strip above the grid.
    columns.forEach((column, index) => {
      const pip = svg("g");
      statusMark(pip, column.status, labelWidth + index * pitch, 0, cellWidth, bandHeight);
      chart.append(pip);
    });

    const cells = [];
    rows.forEach((row, rowIndex) => {
      const y = top + rowIndex * (cellHeight + 2);
      chart.append(svg("text", {
        class: "row-label",
        x: 0,
        y: y + cellHeight / 2 + 4,
        text: row.stage.length > 22 ? `${row.stage.slice(0, 21)}…` : row.stage,
      }));
      cells.push([]);

      row.cells.forEach((cell, columnIndex) => {
        const column = columns[columnIndex];
        const x = labelWidth + columnIndex * pitch;
        const node = svg("g", { role: "gridcell" });

        if (!cell || !cell.attempts.length) {
          const value = cell ? cell.status : "BLOCKED";
          node.append(svg("rect", { x: x + 0.5, y: y + 0.5, width: cellWidth - 1, height: cellHeight - 1, rx: 2, class: "fill-skipped" }));
          tip(node, () => ({
            title: `${row.stage} · ${cell ? status(value).label : "not part of this run"}`,
            rows: [
              ["commit", `${shortSha(column.commit)} ${column.subject || ""}`],
              ["queued", timestamp(column.queued_at)],
            ],
          }), `${row.stage}, commit ${shortSha(column.commit)}: ${cell ? status(value).label : "not run"}`);
        } else {
          const attempts = cell.attempts;
          const gap = 1;
          const segment = (cellWidth - gap * (attempts.length - 1)) / attempts.length;
          attempts.forEach((attempt, attemptIndex) => {
            statusMark(node, attempt.status, x + attemptIndex * (segment + gap), y, segment, cellHeight);
          });
          if (attempts.length > 1) {
            node.append(svg("path", {
              class: "retry-mark",
              d: `M${x + cellWidth - 7} ${y}L${x + cellWidth} ${y}L${x + cellWidth} ${y + 7}Z`,
            }));
          }
          const summaryText = attempts.map((attempt) => status(attempt.status).label).join(" then ");
          tip(node, () => ({
            title: `${row.stage} · ${status(cell.status).label}`,
            rows: [
              ["commit", `${shortSha(column.commit)} ${column.subject || ""}`],
              ["branch", column.branch || "-"],
              ["queued", timestamp(column.queued_at)],
              ...attempts.map((attempt) => [
                `attempt ${attempt.number}`,
                `${status(attempt.status).label}, ${seconds(attempt.duration_seconds)}${attempt.exit_code === null ? "" : `, exit ${attempt.exit_code}`}`,
              ]),
            ],
          }), `${row.stage}, commit ${shortSha(column.commit)}: ${summaryText}`);
        }

        node.append(svg("rect", {
          class: "focus-ring",
          x: x - 1, y: y - 1, width: cellWidth + 2, height: cellHeight + 2, rx: 3,
        }));
        node.dataset.row = String(rowIndex);
        node.dataset.column = String(columnIndex);
        node.setAttribute("tabindex", rowIndex === 0 && columnIndex === columns.length - 1 ? "0" : "-1");
        cells[rowIndex].push(node);
        chart.append(node);
      });
    });

    // Commit labels under the grid, spaced so they never collide.
    const axisY = top + rows.length * (cellHeight + 2) + 14;
    let lastLabel = -Infinity;
    for (const index of groupStart) {
      const x = labelWidth + index * pitch;
      if (x - lastLabel < 64) continue;
      chart.append(svg("text", { class: "axis-label", x, y: axisY, text: shortSha(columns[index].commit) }));
      lastLabel = x;
    }
    chart.append(svg("text", { class: "axis-label", x: 0, y: axisY, text: "commit" }));

    chart.addEventListener("keydown", (event) => {
      const current = event.target.closest("[data-row]");
      if (!current) return;
      let row = Number(current.dataset.row);
      let column = Number(current.dataset.column);
      const moves = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0], Home: [0, -Infinity], End: [0, Infinity] };
      const move = moves[event.key];
      if (!move) return;
      event.preventDefault();
      row = Math.max(0, Math.min(cells.length - 1, row + move[0]));
      column = Math.max(0, Math.min(columns.length - 1, column + move[1]));
      const target = cells[row][column];
      current.setAttribute("tabindex", "-1");
      target.setAttribute("tabindex", "0");
      target.focus();
    });

    container.append(chart);

    for (const [label, value, retried] of [
      ["passed", "SUCCEEDED"], ["failed", "FAILED"], ["timed out", "TIMED_OUT"],
      ["cancelled", "CANCELLED"], ["skipped", "BLOCKED"], ["passed on retry", null, true],
    ]) {
      legend.append(el("span", { class: "legend-item" }, [legendSwatch(value, retried), label]));
    }
    legend.append(el("span", { class: "legend-item", text: "Split cells are separate attempts; top strip is the run result." }));
  }

  /* Pipeline graph with last status and risk. */

  function predictionByStage() {
    const map = new Map();
    const predictions = state.predictions;
    if (predictions && predictions.enabled) {
      for (const item of predictions.predictions) map.set(item.stage, item);
    }
    return map;
  }

  function renderDag() {
    const container = clear($("dag"));
    $("dag-meta").textContent = "";
    const insights = state.insights;
    if (!insights) {
      noSelection(container);
      return;
    }

    const stages = insights.stages;
    const risk = predictionByStage();
    const depth = new Map();
    for (const stage of stages) {
      const parents = stage.depends_on.map((name) => depth.get(name) ?? 0);
      depth.set(stage.name, stage.depends_on.length ? Math.max(...parents) + 1 : 0);
    }

    const layers = [];
    for (const stage of stages) {
      const layer = depth.get(stage.name);
      (layers[layer] = layers[layer] || []).push(stage);
    }

    const nodeWidth = 172;
    const nodeHeight = 46;
    const columnGap = 44;
    const rowGap = 12;
    const tallest = Math.max(...layers.map((layer) => layer.length));
    const width = layers.length * (nodeWidth + columnGap) - columnGap + 2;
    const height = tallest * (nodeHeight + rowGap) - rowGap + 2;
    const position = new Map();

    layers.forEach((layer, layerIndex) => {
      const offset = ((tallest - layer.length) * (nodeHeight + rowGap)) / 2;
      layer.forEach((stage, index) => {
        position.set(stage.name, {
          x: 1 + layerIndex * (nodeWidth + columnGap),
          y: 1 + offset + index * (nodeHeight + rowGap),
        });
      });
    });

    $("dag-meta").textContent = `${stages.length} stages · ${risk.size ? "risk from model" : "failure rate from history"}`;
    const chart = svg("svg", { width, height, role: "group", "aria-label": "Pipeline stages and dependencies" });

    for (const stage of stages) {
      const to = position.get(stage.name);
      for (const dependency of stage.depends_on) {
        const from = position.get(dependency);
        if (!from) continue;
        const x1 = from.x + nodeWidth;
        const y1 = from.y + nodeHeight / 2;
        const x2 = to.x;
        const y2 = to.y + nodeHeight / 2;
        const mid = (x1 + x2) / 2;
        chart.append(svg("path", { class: "edge", d: `M${x1} ${y1}C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}` }));
      }
    }

    for (const stage of stages) {
      const { x, y } = position.get(stage.name);
      const prediction = risk.get(stage.name);
      const node = svg("g");
      node.append(svg("rect", { class: "node-box", x, y, width: nodeWidth, height: nodeHeight, rx: 3 }));
      node.append(svg("rect", { class: `fill-${stage.last_status ? status(stage.last_status).key : "skipped"}`, x: x + 0.5, y: y + 0.5, width: 4, height: nodeHeight - 1 }));
      const name = stage.name.length > 21 ? `${stage.name.slice(0, 20)}…` : stage.name;
      node.append(svg("text", { class: "node-name", x: x + 12, y: y + 18, text: name }));
      const detail = prediction
        ? `risk ${prediction.score.toFixed(2)}${prediction.flagged ? " ▲" : ""}`
        : `fail ${percent(stage.failure_rate)} of ${stage.executions}`;
      node.append(svg("text", { x: x + 12, y: y + 35, text: `${stage.last_status ? status(stage.last_status).label.split(",")[0] : "never run"} · ${detail}` }));
      node.append(svg("rect", { class: "focus-ring", x: x - 2, y: y - 2, width: nodeWidth + 4, height: nodeHeight + 4, rx: 4 }));
      tip(node, () => ({
        title: stage.name,
        rows: [
          ["command", stage.command.join(" ")],
          ["depends on", stage.depends_on.join(", ") || "none"],
          ["last status", stage.last_status ? status(stage.last_status).label : "never run"],
          ["failure rate", `${percent(stage.failure_rate, 1)} of ${stage.executions} runs`],
          ["retries", String(stage.retry_count)],
          ["timeout", `${stage.timeout_seconds} s`],
          ["risk score", prediction ? `${prediction.score.toFixed(3)} (uncalibrated)` : "model not enabled"],
        ],
      }));
      chart.append(node);
    }

    container.append(chart);
  }

  /* Stage failure risk. */

  function renderRisk() {
    const container = clear($("risk"));
    $("risk-meta").textContent = "";
    const predictions = state.predictions;

    if (!state.insights) {
      noSelection(container);
      return;
    }

    if (!predictions || !predictions.enabled) {
      const reason = predictions && (predictions.reason || predictions.error);
      empty(
        container,
        "Failure-risk scores are off",
        reason ? `Server said: ${reason}.` : "",
        ["Export history with ", { code: "python -m buildscope.datasets.cli --database data/buildscope.db --output data/dataset" }, ", train with ", { code: "python -m buildscope.ml.cli --dataset data/dataset --output data/model" }, ", then restart with ", { code: "buildscope serve --model data/model" }, "."],
        "Scores are read-only: the model never queues or runs anything.",
      );
      return;
    }

    const holdout = predictions.holdout;
    const auc = holdout && typeof holdout.roc_auc === "number" ? holdout.roc_auc : null;
    $("risk-meta").textContent = `uncalibrated · holdout AUC ${auc === null ? "n/a" : auc.toFixed(2)}`;
    $("risk-meta").title = `model sha256 ${predictions.model_sha256}…, schema ${predictions.model}`;
    const items = [...predictions.predictions].sort((a, b) => a.position - b.position);
    const labelWidth = 150;
    const width = Math.max(260, container.clientWidth - 24);
    const track = width - labelWidth - 52;
    const rowHeight = 22;
    const chart = svg("svg", {
      width,
      height: items.length * rowHeight + 22,
      role: "group",
      "aria-label": "Failure risk score per stage, uncalibrated",
    });
    const threshold = predictions.threshold ?? 0.5;

    items.forEach((item, index) => {
      const y = index * rowHeight;
      chart.append(svg("text", { class: "row-label", x: 0, y: y + 14, text: item.stage.length > 20 ? `${item.stage.slice(0, 19)}…` : item.stage }));
      const node = svg("g");
      node.append(svg("rect", { class: "track", x: labelWidth, y: y + 4, width: track, height: 12, rx: 2 }));
      node.append(svg("rect", {
        class: `risk-bar${item.flagged ? " flagged" : ""}`,
        x: labelWidth, y: y + 4, width: Math.max(2, item.score * track), height: 12, rx: 2,
      }));
      node.append(svg("text", { x: labelWidth + track + 8, y: y + 14, text: item.score.toFixed(2) }));
      node.append(svg("rect", { class: "focus-ring", x: labelWidth - 2, y: y + 2, width: track + 4, height: 16, rx: 3 }));
      tip(node, () => ({
        title: item.stage,
        rows: [
          ["score", `${item.score.toFixed(4)} (uncalibrated)`],
          ["threshold", threshold.toFixed(2)],
          ["flagged", item.flagged ? "yes, at or above threshold" : "no"],
        ],
      }));
      chart.append(node);
    });

    const thresholdX = labelWidth + threshold * track;
    chart.append(svg("line", { class: "threshold", x1: thresholdX, x2: thresholdX, y1: 0, y2: items.length * rowHeight }));
    chart.append(svg("text", { class: "axis-label", x: thresholdX, y: items.length * rowHeight + 14, "text-anchor": "middle", text: `threshold ${threshold}` }));
    container.append(chart);
    if (auc === null || auc < 0.6) {
      container.append(el("p", { class: "notice" }, [
        auc === null
          ? "This model has no usable holdout ROC AUC. Treat these scores as unvalidated."
          : `On its ${holdout.rows}-row holdout this model ranked failures no better than chance (ROC AUC ${auc.toFixed(2)}). Treat these scores as noise until it is retrained on more history.`,
      ]));
    }
    container.append(el("p", {
      class: "empty",
      text: "Scores rank stages by features of their past runs. They are not probabilities, and the bar colour only marks scores at or above the threshold.",
    }));
  }

  /* Commit timeline and regression windows. */

  function renderTimeline() {
    const container = clear($("timeline"));
    const list = clear($("windows"));
    $("timeline-meta").textContent = "";
    const insights = state.insights;

    if (!insights) {
      noSelection(container);
      return;
    }

    const commits = insights.commits;
    if (!commits.length) {
      empty(
        container,
        "No commits linked to runs",
        "Runs are linked to commits when the repository is a Git checkout with at least one commit. Regression windows appear when a clean commit fails after the previous commit on the same branch passed.",
      );
      return;
    }

    const windows = insights.regressions;
    $("timeline-meta").textContent = `${commits.length} commits · ${windows.filter((item) => item.open).length} open windows`;
    const index = new Map(commits.map((commit, position) => [commit.commit, position]));
    const width = Math.max(container.clientWidth - 24, commits.length * 12 + 40);
    const step = (width - 24) / Math.max(1, commits.length - 1);
    const x = (position) => 12 + position * step;
    const baseY = 34;
    const chart = svg("svg", { width, height: 70, role: "group", "aria-label": "Commit timeline with regression windows" });

    for (const window of windows) {
      const start = index.get(window.last_good.commit);
      const end = index.get(window.first_bad.commit);
      if (start === undefined || end === undefined) continue;
      chart.append(svg("rect", {
        class: `window-band${window.open ? "" : " closed"}`,
        x: x(start), y: 10, width: Math.max(2, x(end) - x(start)), height: 48,
      }));
      if (window.open) {
        chart.append(svg("rect", { class: "window-band closed", x: x(end), y: 10, width: width - x(end), height: 48 }));
      }
      chart.append(svg("line", { class: "window-edge", x1: x(end), x2: x(end), y1: 6, y2: 62 }));
      chart.append(svg("text", { class: "slowdown-label", x: x(end) + 4, y: 8, text: window.open ? "regression, still failing" : "regression" }));
    }

    chart.append(svg("line", { class: "baseline", x1: 0, x2: width, y1: baseY, y2: baseY }));

    commits.forEach((commit, position) => {
      const info = status(commit.last_status);
      const node = svg("g");
      const cx = x(position);
      if (info.key === "pass") {
        node.append(svg("circle", { class: "fill-pass", cx, cy: baseY, r: 4.5 }));
      } else {
        const mark = svg("g");
        statusMark(mark, commit.last_status, cx - 5, baseY - 5, 10, 10);
        node.append(mark);
      }
      node.append(svg("rect", { class: "focus-ring", x: cx - 7, y: baseY - 7, width: 14, height: 14, rx: 3 }));
      tip(node, () => ({
        title: `${shortSha(commit.commit)} ${commit.subject || ""}`,
        rows: [
          ["commit", commit.commit],
          ["branch", commit.branch || "-"],
          ["committed", timestamp(commit.committed_at)],
          ["runs", `${commit.runs}, ${commit.failed_runs} failed`],
          ["latest run", info.label],
        ],
      }), `commit ${shortSha(commit.commit)} ${commit.subject || ""}: latest run ${info.label}`);
      chart.append(node);
    });

    // Keep the newest commit labelled; drop earlier labels that would touch it.
    const labelled = [];
    commits.forEach((commit, position) => {
      const cx = x(position);
      if (!labelled.length || cx - x(labelled[labelled.length - 1]) >= 84) labelled.push(position);
    });
    const last = commits.length - 1;
    while (labelled.length && x(last) - x(labelled[labelled.length - 1]) < 84 && labelled[labelled.length - 1] !== last) labelled.pop();
    if (labelled[labelled.length - 1] !== last) labelled.push(last);
    for (const position of labelled) {
      const anchor = position === 0 ? "start" : position === last ? "end" : "middle";
      chart.append(svg("text", { class: "axis-label", x: x(position), y: 64, "text-anchor": anchor, text: shortSha(commits[position].commit) }));
    }

    container.append(chart);

    if (!windows.length) {
      list.append(el("li", { class: "empty" }, [
        "No regression windows: no clean commit failed after the previous commit on its branch passed.",
      ]));
      return;
    }

    for (const window of [...windows].reverse()) {
      list.append(el("li", { class: `window${window.open ? "" : " closed"}` }, [
        el("span", { class: "tag", text: `${window.open ? "open" : "recovered"} · ${window.branch}` }),
        el("span", { class: "mono", text: `last good ${shortSha(window.last_good.commit)}` }),
        el("span", { class: "subject", title: window.last_good.subject || "", text: window.last_good.subject || "" }),
        el("span", { class: "mono", text: `first bad ${shortSha(window.first_bad.commit)}` }),
        el("span", { class: "subject", title: window.first_bad.subject || "", text: window.first_bad.subject || "" }),
      ]));
    }
  }

  /* Unstable tests with outcome sparklines. */

  function sparkline(history) {
    const tick = 5;
    const height = 16;
    const chart = svg("svg", { width: Math.max(tick * history.length, 10), height, "aria-hidden": "true" });
    history.forEach((entry, position) => {
      const x = position * tick;
      if (entry.outcome === "PASSED") {
        chart.append(svg("rect", { class: "fill-pass", x, y: height - 6, width: tick - 1, height: 6, rx: 1 }));
      } else if (entry.outcome === "FAILED" || entry.outcome === "ERROR") {
        chart.append(svg("rect", { class: "fill-fail", x, y: 0, width: tick - 1, height, rx: 1 }));
      } else {
        chart.append(svg("rect", { class: "fill-skipped", x: x + 0.5, y: height - 6.5, width: tick - 2, height: 5, rx: 1 }));
      }
    });
    return chart;
  }

  function renderFlaky() {
    const container = clear($("flaky"));
    $("flaky-meta").textContent = "";
    const insights = state.insights;

    if (!insights) {
      noSelection(container);
      return;
    }

    const tests = insights.flaky_tests;
    if (!tests.length) {
      empty(
        container,
        insights.test_identities ? "No unstable tests" : "No test results recorded",
        insights.test_identities
          ? `All ${insights.test_identities} tracked tests passed or failed consistently.`
          : ["Add ", { code: "test_report: pytest" }, " (or ", { code: "ctest" }, ", ", { code: "gtest" }, ") to a stage so BuildScope parses its results. A test is classified once it has run at least twice."],
      );
      return;
    }

    $("flaky-meta").textContent = `${insights.summary.flaky_tests} flaky · last ${tests[0].history.length || 0} outcomes, oldest left`;
    const body = el("tbody");
    for (const test of tests) {
      const info = CLASSIFICATION[test.classification] || { key: "neutral", label: test.classification.toLowerCase() };
      const failed = test.history.filter((entry) => entry.outcome === "FAILED" || entry.outcome === "ERROR").length;
      const spark = el("span", {}, [sparkline(test.history)]);
      tip(spark, () => ({
        title: test.name,
        rows: [
          ["last outcomes", test.history.map((entry) => entry.outcome[0]).join("")],
          ["in window", `${test.history.length - failed} passed, ${failed} failed`],
          ["key", "P passed · F failed · E error · S skipped"],
        ],
      }), `${test.name}: last ${test.history.length} outcomes, ${failed} failed`);
      body.append(el("tr", {}, [
        el("td", { class: "test-name mono", title: test.name, text: test.name.split(" / ").slice(-1)[0] }),
        el("td", {}, [el("span", { class: "chip", title: info.title }, [el("span", { class: `dot square bg-${info.key}`, "aria-hidden": "true" }), info.label])]),
        el("td", { class: "num", text: percent(test.failure_rate, 1) }),
        el("td", { class: "num", text: String(test.distinct_runs) }),
        el("td", { class: "num", text: String(test.retry_recoveries) }),
        el("td", {}, [spark]),
      ]));
    }

    container.append(el("table", {}, [
      el("thead", {}, [el("tr", {}, [
        el("th", { scope: "col", text: "Test" }),
        el("th", { scope: "col", text: "Evidence" }),
        el("th", { scope: "col", class: "num", text: "Fail rate" }),
        el("th", { scope: "col", class: "num", text: "Runs" }),
        el("th", { scope: "col", class: "num", text: "Retried", title: "Runs where the test failed and then passed on retry" }),
        el("th", { scope: "col", text: "Recent" }),
      ])]),
      body,
    ]));
  }

  /* Benchmarks: median per revision, MAD band, slowdown markers. */

  function niceTicks(low, high, count) {
    const span = high - low || Math.abs(high) || 1;
    const raw = span / count;
    const magnitude = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((value) => value >= raw);
    const ticks = [];
    for (let value = Math.ceil(low / step) * step; value <= high + step * 1e-9; value += step) ticks.push(value);
    return ticks;
  }

  function renderBenchmarks() {
    const container = clear($("bench"));
    $("bench-meta").textContent = "";
    const insights = state.insights;

    if (!insights) {
      noSelection(container);
      return;
    }

    const series = insights.benchmarks;
    if (!series.length) {
      empty(
        container,
        "No benchmark samples",
        ["Set ", { code: "benchmark_iterations: 9" }, " (5 to 30) on a stage to time it. Revisions are compared once two clean commits on one branch have samples."],
      );
      return;
    }

    const slowdowns = series.flatMap((item) => item.points).filter((point) => point.comparison && point.comparison.slowdown).length;
    $("bench-meta").textContent = `median ± MAD · ${slowdowns} slowdown${slowdowns === 1 ? "" : "s"}`;
    const width = Math.max(280, container.clientWidth - 24);

    for (const item of series) {
      const points = item.points;
      const left = 52;
      const right = 12;
      const top = 22;
      const plotHeight = 110;
      const lows = points.map((point) => point.median_seconds - point.mad_seconds);
      const highs = points.map((point) => point.median_seconds + point.mad_seconds);
      const ticks = niceTicks(Math.min(...lows) * 0.95, Math.max(...highs) * 1.05, 3);
      const low = Math.min(ticks[0], Math.min(...lows));
      const high = Math.max(ticks[ticks.length - 1], Math.max(...highs));
      const y = (value) => top + plotHeight - ((value - low) / (high - low || 1)) * plotHeight;
      const x = (position) => left + (points.length === 1 ? (width - left - right) / 2 : (position * (width - left - right)) / (points.length - 1));

      container.append(el("div", { class: "stat-detail", text: `${item.stage} · ${item.branch}` }));
      const chart = svg("svg", { width, height: top + plotHeight + 26, role: "group", "aria-label": `${item.stage} median duration per revision` });

      for (const value of ticks) {
        chart.append(svg("line", { class: "grid-line", x1: left, x2: width - right, y1: y(value), y2: y(value) }));
        chart.append(svg("text", { class: "axis-label", x: left - 6, y: y(value) + 4, "text-anchor": "end", text: seconds(value) }));
      }

      const upper = points.map((point, position) => `${x(position)},${y(point.median_seconds + point.mad_seconds)}`);
      const lower = points.map((point, position) => `${x(position)},${y(point.median_seconds - point.mad_seconds)}`).reverse();
      chart.append(svg("path", { class: "series-band", d: `M${upper.join("L")}L${lower.join("L")}Z` }));
      chart.append(svg("path", { class: "series-line", d: `M${points.map((point, position) => `${x(position)},${y(point.median_seconds)}`).join("L")}` }));

      points.forEach((point, position) => {
        const node = svg("g");
        const cx = x(position);
        const cy = y(point.median_seconds);
        node.append(svg("circle", { class: "series-point", cx, cy, r: 4 }));
        if (point.comparison && point.comparison.slowdown) {
          node.append(svg("path", { class: "slowdown-mark", d: `M${cx - 5} ${top - 12}L${cx + 5} ${top - 12}L${cx} ${top - 4}Z` }));
          node.append(svg("text", { class: "slowdown-label", x: cx + 8, y: top - 6, text: `+${point.comparison.slowdown_percent.toFixed(1)}%` }));
        }
        node.append(svg("rect", { class: "focus-ring", x: cx - 7, y: cy - 7, width: 14, height: 14, rx: 7 }));
        const comparison = point.comparison;
        tip(node, () => ({
          title: `${shortSha(point.commit)} ${point.subject || ""}`,
          rows: [
            ["median", seconds(point.median_seconds)],
            ["MAD", seconds(point.mad_seconds)],
            ["samples", String(point.samples)],
            ["observed", timestamp(point.observed_at)],
            ...(comparison
              ? [
                ["vs", shortSha(comparison.baseline_commit)],
                ["change", `${comparison.slowdown_percent >= 0 ? "+" : ""}${comparison.slowdown_percent.toFixed(2)}%`],
                ["assessment", comparison.assessment.toLowerCase().replaceAll("_", " ")],
              ]
              : [["assessment", "first revision, nothing to compare"]]),
          ],
        }), `${shortSha(point.commit)}: median ${seconds(point.median_seconds)}${comparison && comparison.slowdown ? `, slowdown ${comparison.slowdown_percent.toFixed(1)}%` : ""}`);
        chart.append(node);
      });

      chart.append(svg("text", { class: "axis-label", x: x(0), y: top + plotHeight + 18, text: shortSha(points[0].commit) }));
      if (points.length > 1) {
        chart.append(svg("text", { class: "axis-label", x: x(points.length - 1), y: top + plotHeight + 18, "text-anchor": "end", text: shortSha(points[points.length - 1].commit) }));
      }
      container.append(chart);
    }
  }

  /* ---------- Startup ---------- */

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => state.overview && renderAll(), 150);
  });

  $("refresh").addEventListener("click", () => loadOverview(state.repositoryId));

  loadOverview(readHash().repository);
})();
