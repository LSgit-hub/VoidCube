(function () {
  "use strict";

  var serviceUrl = (window.GOAL_SERVICE_URL || "http://127.0.0.1:6003").replace(/\/+$/, "");
  var state = {
    projects: [],
    project: null,
    focus: null,
    selected: null,
    backStack: [],
    forwardStack: [],
    zoom: 1,
    loading: false,
    overviewMode: "parents_only",
    overview: null,
    editing: false,
    eventSource: null,
    lastEventId: null,
    overviewRequestId: 0,
    pollTimer: null,
    streamRetryTimer: null,
    menuNode: null,
    dialogAction: null,
    history: null,
    reviewQueue: []
  };
  var $ = function (id) { return document.getElementById(id); };
  var svgNs = "http://www.w3.org/2000/svg";

  function api(path, options) {
    options = options || {};
    var headers = { Accept: "application/json" };
    if (options.body) headers["Content-Type"] = "application/json";
    return fetch(serviceUrl + path, {
      method: options.method || "GET",
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined
    }).then(function (response) {
      return response.text().then(function (text) {
        var payload = text ? JSON.parse(text) : {};
        if (!response.ok) {
          var error = new Error(payload.detail || "Goal Service 请求失败");
          error.payload = payload;
          error.status = response.status;
          throw error;
        }
        return payload;
      });
    });
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function percent(value) {
    return Math.round(value * 100) + "%";
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function progressColor(progress) {
    return "hsl(" + Math.round(clamp(progress, 0, 1) * 120) + " 72% 55%)";
  }

  function statusLabel(status) {
    return {
      planned: "计划中",
      in_progress: "进行中",
      blocked: "阻塞",
      waiting_review: "待评审",
      completed: "已完成",
      cancelled: "已取消"
    }[status] || status;
  }

  function typeLabel(type) {
    return {
      project: "PROJECT",
      objective: "OBJECTIVE",
      milestone: "MILESTONE",
      feature: "FEATURE",
      task: "TASK",
      bug: "BUG",
      test: "TEST",
      release: "RELEASE"
    }[type] || String(type || "").toUpperCase();
  }

  function normalizeNode(node) {
    if (!node) return null;
    return {
      id: node.id,
      projectId: node.project_id || node.projectId,
      nodeType: node.node_type || node.nodeType,
      title: node.title || "未命名目标",
      description: node.description || "",
      status: node.status || "planned",
      progress: clamp(Number(node.progress || 0), 0, 1),
      progressMode: node.progress_mode || node.progressMode || "manual",
      confidence: clamp(Number(node.confidence == null ? 1 : node.confidence), 0, 1),
      priority: Number(node.priority || 0),
      dueAt: node.due_at || node.dueAt || "",
      version: Number(node.version || 1),
      acceptanceCriteria: node.acceptance_criteria || node.acceptanceCriteria || [],
      evidence: node.evidence || [],
      events: node.events || [],
      lifecycle: node.lifecycle || null
    };
  }

  function normalizeLifecycle(payload) {
    payload = payload || {};
    return {
      nodeId: payload.node_id || payload.nodeId,
      executionResults: payload.execution_results || payload.executionResults || [],
      observations: payload.observations || [],
      evidenceVerifications: payload.evidence_verifications || payload.evidenceVerifications || [],
      resultAcceptances: payload.result_acceptances || payload.resultAcceptances || []
    };
  }

  function normalizeProject(project) {
    if (!project) return null;
    return {
      id: project.id,
      name: project.name || "未命名项目",
      description: project.description || "",
      rootNodeId: project.root_node_id || project.rootNodeId,
      progress: clamp(Number(project.progress || 0), 0, 1)
    };
  }

  function setStatus(text, isError) {
    $("status-text").textContent = text;
    $("status-pulse").classList.toggle("error", Boolean(isError));
  }

  function setLoading(loading) {
    state.loading = loading;
    $("loading-state").hidden = !loading;
  }

  function updateHistoryButtons() {
    var history = state.history || {};
    $("undo-button").disabled = !history.can_undo;
    $("redo-button").disabled = !history.can_redo;
  }

  function loadHistory() {
    if (!state.project) {
      state.history = null;
      updateHistoryButtons();
      return Promise.resolve();
    }
    return api("/api/goals/projects/" + encodeURIComponent(state.project.id) + "/history")
      .then(function (payload) {
        state.history = payload;
        updateHistoryButtons();
      })
      .catch(function (error) {
        state.history = null;
        updateHistoryButtons();
        setStatus(error.message || "历史状态加载失败", true);
      });
  }

  function showError(error) {
    setStatus(error && error.message ? error.message : "Goal Service 不可用", true);
    $("loading-state").textContent = "无法连接 Goal Service，请确认 6003 服务正在运行。";
    $("loading-state").hidden = false;
  }

  function stopEventStream() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    if (state.streamRetryTimer) {
      window.clearTimeout(state.streamRetryTimer);
      state.streamRetryTimer = null;
    }
  }

  function pollEvents() {
    if (!state.project) return;
    var path = "/api/goals/events?project_id=" + encodeURIComponent(state.project.id);
    if (state.lastEventId) path += "&after=" + encodeURIComponent(state.lastEventId);
    api(path).then(function (payload) {
      var events = payload.events || [];
      if (!events.length) return;
      state.lastEventId = events[events.length - 1].id || state.lastEventId;
      var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
      setStatus("轮询发现目标更新，正在刷新...", false);
      if (focusedId) loadFocus(focusedId);
      else loadProjects(state.project.id);
    }).catch(function (error) {
      setStatus(error.message || "目标更新检查失败", true);
    });
  }

  function startPollingFallback() {
    if (state.pollTimer || !state.project) return;
    state.pollTimer = window.setInterval(pollEvents, 30000);
    setStatus("实时连接中断，已启用 30 秒轮询", true);
  }

  function startEventStream() {
    stopEventStream();
    if (!state.project) return;
    var open = function () {
      var query = "?project_id=" + encodeURIComponent(state.project.id);
      if (state.lastEventId) query += "&after=" + encodeURIComponent(state.lastEventId);
      var source = new EventSource(
        serviceUrl + "/api/goals/projects/" + encodeURIComponent(state.project.id) + "/events" + query
      );
      state.eventSource = source;
      source.onopen = function () {
        if (state.pollTimer) {
          window.clearInterval(state.pollTimer);
          state.pollTimer = null;
        }
        setStatus("实时更新已连接", false);
      };
      source.onmessage = function (message) {
        var event;
        try {
          event = JSON.parse(message.data);
        } catch (_error) {
          return;
        }
        state.lastEventId = message.lastEventId || event.id || state.lastEventId;
        setStatus("收到目标更新，正在刷新...", false);
        var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
        if (focusedId) loadFocus(focusedId);
        else loadProjects(state.project.id);
      };
      source.onerror = function () {
        source.close();
        if (state.eventSource === source) state.eventSource = null;
        startPollingFallback();
        if (state.project && !state.eventSource && !state.streamRetryTimer) {
          state.streamRetryTimer = window.setTimeout(function () {
            state.streamRetryTimer = null;
            if (state.project && !state.eventSource) startEventStream();
          }, 3000);
        }
      };
    };
    if (state.lastEventId) {
      open();
      return;
    }
    api("/api/goals/events/latest?project_id=" + encodeURIComponent(state.project.id))
      .then(function (payload) {
        state.lastEventId = payload.event_id || null;
        open();
      }).catch(open);
  }

  function svg(tag, attrs) {
    var element = document.createElementNS(svgNs, tag);
    Object.keys(attrs || {}).forEach(function (key) { element.setAttribute(key, attrs[key]); });
    return element;
  }

  function stableHash(value) {
    var hash = 2166136261;
    for (var index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0) / 4294967296;
  }

  function shortTitle(title, max) {
    var chars = Array.from(String(title || ""));
    return chars.length > max ? chars.slice(0, max - 1).join("") + "…" : chars.join("");
  }

  function makeNodeGroup(node, x, y, radius, focused) {
    var circumference = 2 * Math.PI * (radius + 10);
    var group = svg("g", {
      "class": "node-group",
      "data-node-id": node.id,
      tabindex: "0",
      role: "button",
      "aria-label": node.title + " " + percent(node.progress)
    });
    var arc = svg("circle", {
      "class": "progress-arc",
      cx: x,
      cy: y,
      r: radius + 10,
      "stroke-dasharray": (node.progress * circumference) + " " + circumference
    });
    var halo = focused ? svg("circle", { "class": "focus-halo", cx: x, cy: y, r: radius + 20 }) : null;
    var ring = svg("circle", {
      "class": "status-ring status-" + node.status.replace(/_/g, "-"),
      cx: x, cy: y, r: radius + 5
    });
    var core = svg("circle", {
      "class": "node-core" + (focused ? " focus-core" : ""),
      cx: x, cy: y, r: radius, fill: progressColor(node.progress)
    });
    var label = svg("text", { "class": "node-label", x: x, y: y + radius + 28, "text-anchor": "middle" });
    label.textContent = shortTitle(node.title, focused ? 24 : 16);
    var progressLabel = svg("text", {
      "class": "node-progress-label", x: x, y: y + radius + 44, "text-anchor": "middle"
    });
    progressLabel.textContent = statusLabel(node.status) + " · " + percent(node.progress);
    if (halo) group.appendChild(halo);
    group.appendChild(arc);
    group.appendChild(ring);
    group.appendChild(core);
    group.appendChild(label);
    group.appendChild(progressLabel);
    group.addEventListener("click", function () {
      if (focused) selectNode(node.id);
      else focusNode(node.id, true);
    });
    group.addEventListener("contextmenu", function (event) {
      event.preventDefault();
      openNodeMenu(node, event.clientX, event.clientY);
    });
    group.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        group.click();
      }
    });
    return group;
  }

  function renderRadial() {
    var root = $("radial-content");
    while (root.firstChild) root.removeChild(root.firstChild);
    $("radial-svg").style.transform = "scale(" + state.zoom + ")";
    $("zoom-label").textContent = Math.round(state.zoom * 100) + "%";
    var focus = state.focus && normalizeNode(state.focus.focus);
    if (!focus) {
      $("empty-state").hidden = false;
      $("focus-heading").textContent = "没有焦点目标";
      return;
    }
    $("empty-state").hidden = true;
    var children = (state.focus.children || []).map(normalizeNode).filter(Boolean);
    var cx = 450;
    var cy = 305;
    var radius = children.length > 30 ? 58 : 68;
    var orbit = children.length <= 12 ? 205 : 190;
    root.appendChild(svg("circle", { "class": "radial-background", cx: cx, cy: cy, r: orbit }));
    root.appendChild(svg("circle", { "class": "radial-background", cx: cx, cy: cy, r: orbit + 58 }));
    children.forEach(function (node, index) {
      var doubleRing = children.length > 12;
      var ringIndex = doubleRing ? index % 2 : 0;
      var ringCount = doubleRing ? Math.ceil(children.length / 2) : children.length;
      var slot = doubleRing ? Math.floor(index / 2) : index;
      var angle = (stableHash(node.id) * Math.PI * 2 +
        (slot / Math.max(ringCount, 1)) * Math.PI * 2) % (Math.PI * 2);
      var currentOrbit = doubleRing ? orbit + ringIndex * 58 : orbit;
      var x = cx + Math.cos(angle) * currentOrbit;
      var y = cy + Math.sin(angle) * currentOrbit;
      root.appendChild(svg("line", { "class": "radial-link", x1: cx, y1: cy, x2: x, y2: y }));
      root.appendChild(makeNodeGroup(node, x, y, radius, false));
    });
    root.appendChild(makeNodeGroup(focus, cx, cy, 82, true));
    $("focus-heading").textContent = focus.title;
    $("project-progress").textContent = "项目进度 " + percent(state.project ? state.project.progress : focus.progress);
    renderBreadcrumb(focus);
    updateNavigationButtons();
  }

  function renderBreadcrumb(focus) {
    var crumb = $("breadcrumb");
    crumb.innerHTML = "";
    if (state.project) {
      var projectSpan = document.createElement("span");
      projectSpan.className = "crumb";
      projectSpan.textContent = state.project.name;
      crumb.appendChild(projectSpan);
      var separator = document.createElement("span");
      separator.className = "crumb-separator";
      separator.textContent = "/";
      crumb.appendChild(separator);
    }
    var current = document.createElement("span");
    current.className = "crumb crumb-current";
    current.textContent = focus.title;
    crumb.appendChild(current);
  }

  function updateNavigationButtons() {
    $("back-button").disabled = state.backStack.length === 0;
    $("forward-button").disabled = state.forwardStack.length === 0;
  }

  function loadFocus(nodeId) {
    if (!state.project) return Promise.resolve();
    setLoading(true);
    var path = "/api/goals/projects/" + encodeURIComponent(state.project.id) + "/focus";
    if (nodeId) path += "?node=" + encodeURIComponent(nodeId);
    return api(path).then(function (payload) {
      state.focus = payload;
      setLoading(false);
      setStatus("目标图已更新", false);
      renderRadial();
      return Promise.all([loadOverview(), loadHistory(), loadProjectSummary(), loadReviewQueue()]).then(function () {
        renderDetail(state.selected);
      });
    }).catch(function (error) {
      setLoading(false);
      showError(error);
    });
  }

  function loadOverview() {
    if (!state.project) return Promise.resolve();
    var path = "/api/goals/projects/" + encodeURIComponent(state.project.id) +
      "/overview?mode=" + encodeURIComponent(state.overviewMode);
    return api(path).then(function (payload) {
      state.overview = payload;
      renderOverview();
    }).catch(function (error) {
      setStatus(error.message || "总览加载失败", true);
    });
  }

  function loadProjectSummary() {
    if (!state.project) return Promise.resolve();
    return api("/api/goals/projects/" + encodeURIComponent(state.project.id)).then(function (payload) {
      state.project = normalizeProject(payload);
      renderRadial();
    });
  }

  function focusNode(nodeId, pushHistory) {
    var current = state.focus && state.focus.focus && state.focus.focus.id;
    if (pushHistory && current && current !== nodeId) {
      state.backStack.push(current);
      state.forwardStack = [];
    }
    state.selected = null;
    state.editing = false;
    window.history.replaceState({ nodeId: nodeId }, "", "#" + encodeURIComponent(nodeId));
    return loadFocus(nodeId);
  }

  function selectNode(nodeId) {
    if (!state.focus) return;
    var focus = normalizeNode(state.focus.focus);
    var child = (state.focus.children || []).map(normalizeNode).find(function (node) {
      return node && node.id === nodeId;
    });
    state.selected = focus && focus.id === nodeId ? focus : child || null;
    state.editing = false;
    renderDetail(state.selected);
    if (state.selected) loadNodeDetail(state.selected.id);
  }

  function loadNodeDetail(nodeId) {
    return Promise.all([
      api("/api/goals/nodes/" + encodeURIComponent(nodeId)),
      api("/api/goals/nodes/" + encodeURIComponent(nodeId) + "/lifecycle")
    ]).then(function (results) {
      var node = normalizeNode(results[0]);
      node.lifecycle = normalizeLifecycle(results[1]);
      if (state.selected && state.selected.id === nodeId) {
        state.selected = Object.assign(state.selected, node);
        renderDetail(state.selected);
      }
      return node;
    }).catch(function (error) {
      setStatus(error.message || "详情加载失败", true);
      return null;
    });
  }

  function loadReviewQueue() {
    if (!state.project) {
      state.reviewQueue = [];
      return Promise.resolve();
    }
    return api("/api/goals/projects/" + encodeURIComponent(state.project.id) + "/overview")
      .then(function (payload) {
        state.reviewQueue = (payload.nodes || []).map(normalizeNode).filter(function (node) {
          return node.status === "waiting_review";
        });
      }).catch(function (error) {
        state.reviewQueue = [];
        setStatus(error.message || "待审核队列加载失败", true);
      });
  }

  function navigateBack() {
    if (!state.backStack.length) return;
    var current = state.focus && state.focus.focus && state.focus.focus.id;
    var target = state.backStack.pop();
    if (current) state.forwardStack.push(current);
    focusNode(target, false);
  }

  function navigateForward() {
    if (!state.forwardStack.length) return;
    var current = state.focus && state.focus.focus && state.focus.focus.id;
    var target = state.forwardStack.pop();
    if (current) state.backStack.push(current);
    focusNode(target, false);
  }

  function closeNodeMenu() {
    $("node-menu").hidden = true;
    state.menuNode = null;
  }

  function closeDialog() {
    $("goal-dialog").hidden = true;
    $("dialog-content").innerHTML = "";
    state.dialogAction = null;
  }

  function openDialog(title, content, action, confirmLabel) {
    closeNodeMenu();
    $("dialog-title").textContent = title;
    $("dialog-content").innerHTML = content;
    $("dialog-confirm-button").textContent = confirmLabel || "确认";
    state.dialogAction = action;
    $("goal-dialog").hidden = false;
  }

  function openConfirm(title, message, action, confirmLabel) {
    openDialog(
      title,
      '<p class="dialog-message">' + escapeHtml(message) + "</p>",
      action,
      confirmLabel
    );
  }

  function freshNode(node) {
    return api("/api/goals/nodes/" + encodeURIComponent(node.id))
      .then(function (payload) { return normalizeNode(payload); });
  }

  function latestBatchId(node) {
    var events = node.events || [];
    for (var index = 0; index < events.length; index += 1) {
      var event = events[index];
      var batchId = event.batch_id || event.batchId;
      if (batchId && event.event_type !== "rollback") return batchId;
    }
    return null;
  }

  function openEvidenceDialog(node) {
    openDialog(
      "添加目标证据",
      '<form id="evidence-form" class="dialog-form">' +
      '<label>类型<select id="evidence-type">' +
      ["test_result", "ci_build", "git_commit", "pr", "issue", "note", "file", "manual"].map(function (type) {
        return '<option value="' + type + '">' + escapeHtml(type) + "</option>";
      }).join("") + "</select></label>" +
      '<label>标题<input id="evidence-title" type="text" maxlength="180" placeholder="例如：M4 回归测试"></label>' +
      '<label>地址<input id="evidence-uri" type="text" maxlength="500" placeholder="可选"></label>' +
      '<label>内容<textarea id="evidence-content" maxlength="3000" placeholder="可选"></textarea></label>' +
      '<label>原因<input id="evidence-reason" type="text" maxlength="240" value="通过目标管理界面添加证据"></label>' +
      "</form>",
      function () { submitEvidence(node); },
      "写入证据"
    );
    $("evidence-form").addEventListener("submit", function (event) { event.preventDefault(); submitEvidence(node); });
    $("evidence-title").focus();
  }

  function currentCreateParent() {
    if (state.selected) return normalizeNode(state.selected);
    if (state.focus && state.focus.focus) return normalizeNode(state.focus.focus);
    return null;
  }

  function openCreateChildDialog(parent) {
    if (!parent || !state.project) {
      setStatus("请先选择一个目标", true);
      return;
    }
    if (parent.nodeType === "release") {
      setStatus("发布节点不能继续创建子目标", true);
      return;
    }
    openDialog(
      "新建子目标",
      '<form id="create-child-form" class="dialog-form">' +
      '<label>类型<select id="create-child-type">' +
      ["objective", "milestone", "feature", "task", "bug", "test", "release"].map(function (type) {
        return '<option value="' + type + '"' + (type === "task" ? " selected" : "") + ">" +
          escapeHtml(typeLabel(type)) + "</option>";
      }).join("") + "</select></label>" +
      '<label>标题<input id="create-child-title" type="text" maxlength="160" placeholder="例如：完成训练数据清洗"></label>' +
      '<label>描述<textarea id="create-child-description" maxlength="1000" placeholder="这个子目标要交付什么"></textarea></label>' +
      '<label>原因<input id="create-child-reason" type="text" maxlength="240" value="通过目标管理界面拆解目标"></label>' +
      "</form>",
      function () { submitCreateChild(parent); },
      "创建子目标"
    );
    $("create-child-form").addEventListener("submit", function (event) {
      event.preventDefault();
      submitCreateChild(parent);
    });
    $("create-child-title").focus();
  }

  function submitCreateChild(parent) {
    var title = $("create-child-title").value.trim();
    var reason = $("create-child-reason").value.trim();
    if (!title || !reason) {
      setStatus("子目标标题和原因不能为空", true);
      return;
    }
    setStatus("正在创建子目标...", false);
    api("/api/goals/batch", {
      method: "POST",
      body: {
        project_id: parent.projectId || state.project.id,
        reason: reason,
        created_by: "user",
        actor_type: "user",
        operations: [
          {
            op: "create_node",
            temp_id: "new_child",
            node_type: $("create-child-type").value,
            title: title,
            description: $("create-child-description").value.trim(),
            status: "planned",
            progress: 0,
            progress_mode: "manual",
            confidence: 1,
            priority: 0,
            acceptance_criteria: []
          },
          {
            op: "create_edge",
            source_id: parent.id,
            target_id: "new_child",
            edge_type: "decomposes_to",
            progress_weight: 1,
            required: true
          }
        ]
      }
    }).then(function () {
      closeDialog();
      setStatus("子目标已创建", false);
      return focusNode(parent.id, false).then(function () {
        state.selected = parent;
        state.editing = false;
        renderDetail(parent);
        return loadNodeDetail(parent.id);
      });
    }).catch(showError);
  }

  function submitEvidence(node) {
    var reason = $("evidence-reason").value.trim();
    if (!reason) {
      setStatus("证据原因不能为空", true);
      return;
    }
    setStatus("正在写入证据...", false);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/evidence", {
      method: "POST",
      body: {
        evidence_type: $("evidence-type").value,
        title: $("evidence-title").value.trim(),
        uri: $("evidence-uri").value.trim(),
        content: $("evidence-content").value.trim(),
        reason: reason,
        created_by: "user",
        actor_type: "user"
      }
    }).then(function () {
      closeDialog();
      setStatus("证据已写入", false);
      return refreshAfterNodeChange(node.id);
    }).catch(showError);
  }

  function openVerifyEvidenceDialog(node) {
    var criteria = node.acceptanceCriteria || [];
    if (!criteria.length) {
      setStatus("该目标还没有验收条件", true);
      return;
    }
    openDialog(
      "核验证据",
      '<form id="verify-evidence-form" class="dialog-form">' +
      '<label>验收条件<select id="verify-criterion-index">' +
      criteria.map(function (item, index) {
        return '<option value="' + index + '">' + escapeHtml(criterionTitle(item, index)) + "</option>";
      }).join("") + "</select></label>" +
      '<label>证据<select id="verify-evidence-id"><option value="">不关联证据条目</option>' +
      (node.evidence || []).map(function (item) {
        return '<option value="' + escapeHtml(item.id) + '">' +
          escapeHtml(item.title || item.evidence_type || item.id) + "</option>";
      }).join("") + "</select></label>" +
      '<label>结论<select id="verify-accepted"><option value="true">通过</option><option value="false">退回</option></select></label>' +
      '<label>摘要<textarea id="verify-summary" maxlength="1000" placeholder="核验结论"></textarea></label>' +
      '<label>原因<input id="verify-reason" type="text" maxlength="240" value="通过目标管理界面核验证据"></label>' +
      "</form>",
      function () { submitEvidenceVerification(node); },
      "写入核验"
    );
    $("verify-evidence-form").addEventListener("submit", function (event) {
      event.preventDefault();
      submitEvidenceVerification(node);
    });
    $("verify-summary").focus();
  }

  function submitEvidenceVerification(node) {
    var reason = $("verify-reason").value.trim();
    var summary = $("verify-summary").value.trim();
    if (!reason || !summary) {
      setStatus("核验摘要和原因不能为空", true);
      return;
    }
    setStatus("正在写入核验记录...", false);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/evidence-verifications", {
      method: "POST",
      body: {
        evidence_id: $("verify-evidence-id").value || null,
        accepted: $("verify-accepted").value === "true",
        summary: summary,
        criterion_index: Number($("verify-criterion-index").value),
        reason: reason,
        actor_type: "user"
      }
    }).then(function () {
      closeDialog();
      setStatus("核验记录已写入", false);
      return refreshAfterNodeChange(node.id);
    }).catch(showError);
  }

  function requestRollback(node, confirm) {
    var batchId = latestBatchId(node);
    if (!batchId) {
      setStatus("该目标没有可回滚的批次", true);
      return;
    }
    requestRollbackBatch(batchId, confirm, node.id);
  }

  function requestRollbackBatch(batchId, confirm, nodeId) {
    api("/api/goals/rollback", {
      method: "POST",
      body: {
        batch_id: batchId,
        reason: "通过目标管理界面回滚批次",
        confirm: Boolean(confirm),
        actor_type: "user"
      }
    }).then(function () {
      closeDialog();
      setStatus("批次已回滚", false);
      return refreshAfterNodeChange(nodeId);
    }).catch(function (error) {
      if (error.payload && error.payload.requires_confirm) {
        openConfirm(
          "服务端确认回滚",
          "该批次包含较多变更，服务端要求再次确认后才会回滚。",
          function () { requestRollbackBatch(batchId, true, nodeId); },
          "确认回滚"
        );
        return;
      }
      showError(error);
    });
  }

  function requestRedo(batchId) {
    api("/api/goals/redo", {
      method: "POST",
      body: {
        project_id: state.project && state.project.id,
        batch_id: batchId,
        reason: "通过目标管理界面重做批次",
        actor_type: "user"
      }
    }).then(function () {
      closeDialog();
      setStatus("批次已重做", false);
      var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
      return focusedId ? loadFocus(focusedId) : loadProjects(state.project && state.project.id);
    }).catch(showError);
  }

  function openUndoDialog() {
    var batchId = state.history && state.history.undo_batch_id;
    if (!batchId) return;
    openConfirm(
      "撤销最近批次",
      "撤销会还原最近一批目标变更，并在审计日志中留下回滚事件。",
      function () { requestRollbackBatch(batchId, false); },
      "继续撤销"
    );
  }

  function openRedoDialog() {
    var batchId = state.history && state.history.redo_batch_id;
    if (!batchId) return;
    openConfirm(
      "重做最近批次",
      "重做会重新应用最近一次撤销的目标变更，并在审计日志中留下重做事件。",
      function () { requestRedo(batchId); },
      "继续重做"
    );
  }

  function fallbackFocusId(nodeId) {
    var edges = state.overview && state.overview.edges || [];
    for (var index = 0; index < edges.length; index += 1) {
      var edge = edges[index];
      if (edge.edge_type === "decomposes_to" &&
          (edge.target_id || edge.targetId) === nodeId) {
        return edge.source_id || edge.sourceId;
      }
    }
    return state.project && state.project.rootNodeId;
  }

  function requestDelete(node, confirmToken) {
    var currentFocusId = state.focus && state.focus.focus && state.focus.focus.id;
    var query = "?reason=" + encodeURIComponent("通过目标管理界面删除节点") +
      "&actor_type=user";
    if (confirmToken) query += "&confirm_token=" + encodeURIComponent(confirmToken);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + query, {
      method: "DELETE"
    }).then(function () {
      closeDialog();
      state.selected = null;
      state.editing = false;
      if (currentFocusId === node.id) {
        state.backStack = [];
        state.forwardStack = [];
        return loadFocus(fallbackFocusId(node.id));
      }
      setStatus("节点已删除", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) {
      if (error.payload && error.payload.requires_confirm) {
        openConfirm(
          "服务端确认删除",
          "这是受保护的危险操作，服务端要求确认后才会继续。",
          function () { requestDelete(node, error.payload.confirm_token); },
          "确认删除"
        );
        return;
      }
      showError(error);
    });
  }

  function beginNodeEdit(node) {
    freshNode(node).then(function (fresh) {
      if (!fresh) return;
      state.selected = fresh;
      state.editing = true;
      renderDetail(fresh);
      $("detail-edit-title").focus();
    }).catch(showError);
  }

  function blockNode(node) {
    freshNode(node).then(function (fresh) {
      if (fresh) patchNode(fresh, { status: "blocked" }, "通过目标管理界面标记阻塞");
    }).catch(showError);
  }

  function openNodeMenu(node, clientX, clientY) {
    state.menuNode = node;
    var menu = $("node-menu");
    menu.hidden = false;
    menu.style.left = Math.max(8, Math.min(clientX, window.innerWidth - 176)) + "px";
    menu.style.top = Math.max(8, Math.min(clientY, window.innerHeight - 220)) + "px";
  }

  function handleMenuAction(action) {
    var node = state.menuNode;
    closeNodeMenu();
    if (!node) return;
    if (action === "view") {
      state.selected = normalizeNode(node);
      state.editing = false;
      renderDetail(state.selected);
      loadNodeDetail(node.id);
    } else if (action === "create-child") {
      openCreateChildDialog(normalizeNode(node));
    } else if (action === "edit") {
      beginNodeEdit(node);
    } else if (action === "block") {
      blockNode(node);
    } else if (action === "evidence") {
      freshNode(node).then(function (fresh) { if (fresh) openEvidenceDialog(fresh); }).catch(showError);
    } else if (action === "rollback") {
      freshNode(node).then(function (fresh) {
        if (!fresh || !latestBatchId(fresh)) {
          setStatus("该目标没有可回滚的批次", true);
          return;
        }
        openConfirm(
          "回滚最近批次",
          "回滚会还原该批次的目标变更，并在审计日志中留下回滚事件。",
          function () { requestRollback(fresh, false); },
          "继续回滚"
        );
      }).catch(showError);
    } else if (action === "delete") {
      freshNode(node).then(function (fresh) {
        if (fresh) {
          openConfirm(
            "删除目标节点",
            "节点将被软删除；如果它是项目根节点或包含子节点，服务端还会要求额外确认。",
            function () { requestDelete(fresh); },
            "继续删除"
          );
        }
      }).catch(showError);
    }
  }

  function refreshAfterNodeChange(nodeId) {
    var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
    var refresh = focusedId ? loadFocus(focusedId) : loadProjects(state.project && state.project.id);
    return refresh.then(function () {
      if (state.selected && state.selected.id === nodeId) loadNodeDetail(nodeId);
    });
  }

  function patchNode(node, patch, reason) {
    return api("/api/goals/nodes/" + encodeURIComponent(node.id), {
      method: "PATCH",
      body: {
        expected_version: node.version,
        patch: patch,
        reason: reason,
        actor_type: "user"
      }
    }).then(function (payload) {
      state.selected = normalizeNode(payload.node);
      state.editing = false;
      renderDetail(state.selected);
      setStatus("目标已更新", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) {
      renderDetail(state.selected);
      showError(error);
    });
  }

  function saveNodeEdit(node) {
    var title = $("detail-edit-title").value.trim();
    var reason = $("detail-edit-reason").value.trim();
    if (!title || !reason) {
      setStatus("标题和原因不能为空", true);
      return;
    }
    setStatus("正在保存目标...", false);
    return patchNode(node, {
      title: title,
      description: $("detail-edit-description").value.trim(),
      status: $("detail-edit-status").value,
      progress: Number($("detail-edit-progress").value)
    }, reason);
  }

  function renderDetailForm(node) {
    $("edit-detail-button").hidden = true;
    $("detail-content").innerHTML =
      '<form id="detail-edit-form" class="detail-form">' +
      '<label>标题<input id="detail-edit-title" type="text" maxlength="160" value="' +
      escapeHtml(node.title) + '"></label>' +
      '<label>描述<textarea id="detail-edit-description" maxlength="1000">' +
      escapeHtml(node.description) + '</textarea></label>' +
      '<label>状态<select id="detail-edit-status">' +
      ["planned", "in_progress", "blocked", "waiting_review", "completed", "cancelled"].map(function (status) {
        return '<option value="' + status + '"' + (status === node.status ? " selected" : "") + ">" +
          escapeHtml(statusLabel(status)) + "</option>";
      }).join("") + "</select></label>" +
      '<label>进度<div class="range-row"><input id="detail-edit-progress" type="range" min="0" max="1" step="0.01" value="' +
      node.progress + '"><output id="detail-edit-progress-value" class="range-value">' +
      percent(node.progress) + "</output></div></label>" +
      '<label>原因<input id="detail-edit-reason" type="text" maxlength="240" value="通过目标管理界面编辑目标"></label>' +
      '<div class="detail-form-actions"><button id="cancel-detail-edit" class="button button-quiet" type="button">取消</button>' +
      '<button id="save-detail-edit" class="button button-primary" type="submit">保存</button></div>' +
      "</form>";
    $("detail-edit-progress").addEventListener("input", function (event) {
      $("detail-edit-progress-value").textContent = percent(Number(event.target.value));
    });
    $("detail-edit-form").addEventListener("submit", function (event) {
      event.preventDefault();
      saveNodeEdit(node);
    });
    $("cancel-detail-edit").addEventListener("click", function () {
      state.editing = false;
      renderDetail(node);
    });
  }

  function toggleCriterion(node, index, met) {
    var criteria = (node.acceptanceCriteria || []).map(function (item) {
      return Object.assign({}, item);
    });
    if (!criteria[index]) return;
    criteria[index].met = met;
    patchNode(node, { acceptance_criteria: criteria }, "通过目标管理界面更新验收条件");
  }

  function criterionTitle(item, index) {
    return item && (item.title || item.text || item.description) || "验收条件 " + (index + 1);
  }

  function verificationAfter(event) {
    return event && (event.after || event.after_json || event);
  }

  function appliedVerificationIds(criteria) {
    var result = {};
    (criteria || []).forEach(function (item) {
      if (item && item.verification_id) result[item.verification_id] = true;
    });
    return result;
  }

  function readyForReview(node) {
    var criteria = node.acceptanceCriteria || [];
    return node.status !== "completed" && node.status !== "waiting_review" &&
      node.status !== "cancelled" && node.progress >= 1 && criteria.length > 0 &&
      criteria.every(function (item) { return item && item.met; });
  }

  function reviewQueueHtml() {
    var queue = state.reviewQueue || [];
    if (!queue.length) return "";
    return '<section class="detail-section review-queue"><h3>待审核队列</h3><ul class="review-list">' +
      queue.slice(0, 8).map(function (node) {
        return '<li class="review-item"><button type="button" data-review-node-id="' + escapeHtml(node.id) + '">' +
          '<span>' + escapeHtml(node.title) + '</span><strong>' + percent(node.progress) + '</strong></button></li>';
      }).join("") + '</ul></section>';
  }

  function attachReviewQueueHandlers() {
    document.querySelectorAll("[data-review-node-id]").forEach(function (button) {
      button.addEventListener("click", function () {
        focusNode(button.dataset.reviewNodeId, true).then(function () {
          selectNode(button.dataset.reviewNodeId);
        });
      });
    });
  }

  function lifecycleHtml(node) {
    var lifecycle = normalizeLifecycle(node.lifecycle);
    var criteria = node.acceptanceCriteria || [];
    var applied = appliedVerificationIds(criteria);
    var verifications = lifecycle.evidenceVerifications;
    var verificationHtml = verifications.length ? verifications.slice().reverse().map(function (event) {
      var item = verificationAfter(event) || {};
      var accepted = item.accepted === true;
      var index = item.criterion_index;
      var canApply = accepted && typeof index === "number" && criteria[index] && !applied[item.id];
      return '<li class="lifecycle-item ' + (accepted ? "accepted" : "rejected") + '">' +
        '<div><strong>' + escapeHtml(accepted ? "通过" : "退回") + '</strong><span>' +
        escapeHtml(criterionTitle(criteria[index], index || 0)) + '</span><p>' + escapeHtml(item.summary || "未填写摘要") + '</p></div>' +
        (canApply ? '<button class="button button-quiet lifecycle-apply" type="button" data-verification-id="' +
          escapeHtml(item.id) + '">应用</button>' : "") + '</li>';
    }).join("") : '<li class="lifecycle-item">暂无核验记录</li>';
    var observations = lifecycle.observations.length ? lifecycle.observations.slice(-3).map(function (event) {
      var item = verificationAfter(event) || {};
      return '<li class="lifecycle-note">' + escapeHtml(item.summary || "观察记录") + '</li>';
    }).join("") : '<li class="lifecycle-note">暂无观察记录</li>';
    return '<section class="detail-section"><h3>执行生命周期</h3>' +
      '<ul class="lifecycle-list">' + verificationHtml + '</ul>' +
      '<ul class="lifecycle-notes">' + observations + '</ul>' +
      '<button id="verify-evidence-button" class="button button-quiet detail-section-button" type="button">核验证据</button></section>';
  }

  function requestApplyVerification(node, verificationId) {
    setStatus("正在应用核验结论...", false);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/apply-evidence-verification", {
      method: "POST",
      body: {
        verification_id: verificationId,
        expected_version: node.version,
        reason: "通过目标管理界面应用核验结论",
        actor_type: "user"
      }
    }).then(function () {
      setStatus("核验结论已应用", false);
      return refreshAfterNodeChange(node.id);
    }).catch(showError);
  }

  function requestSubmitForReview(node) {
    setStatus("正在提交审核...", false);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/submit-for-review", {
      method: "POST",
      body: {
        expected_version: node.version,
        reason: "通过目标管理界面提交审核",
        actor_type: "user"
      }
    }).then(function () {
      closeDialog();
      setStatus("目标已进入待审核", false);
      return refreshAfterNodeChange(node.id);
    }).catch(showError);
  }

  function requestApproveReview(node) {
    setStatus("正在批准审核...", false);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/approve-review", {
      method: "POST",
      body: {
        expected_version: node.version,
        reason: "通过目标管理界面批准审核",
        actor_type: "user"
      }
    }).then(function () {
      closeDialog();
      setStatus("审核已批准，目标完成", false);
      return refreshAfterNodeChange(node.id);
    }).catch(showError);
  }

  function requestRejectReview(node) {
    setStatus("正在退回审核...", false);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/reject-review", {
      method: "POST",
      body: {
        expected_version: node.version,
        reason: "通过目标管理界面退回审核",
        actor_type: "user"
      }
    }).then(function () {
      closeDialog();
      setStatus("审核已退回", false);
      return refreshAfterNodeChange(node.id);
    }).catch(showError);
  }

  function renderDetail(node) {
    $("detail-title").textContent = node ? node.title : "选择一个目标";
    $("edit-detail-button").hidden = !node || state.editing;
    if (!node) {
      $("detail-content").innerHTML = reviewQueueHtml() +
        '<div class="detail-placeholder">点击中心节点查看详情。点击子节点可进入下一层目标。</div>';
      attachReviewQueueHandlers();
      return;
    }
    if (state.editing) {
      renderDetailForm(node);
      return;
    }
    var criteria = node.acceptanceCriteria || [];
    var events = node.events || [];
    var evidence = node.evidence || [];
    var batchId = latestBatchId(node);
    var criteriaHtml = criteria.length ? criteria.map(function (item, index) {
      var met = item && item.met;
      return '<li class="criteria-item' + (met ? " met" : "") + '">' +
        '<input type="checkbox" data-criterion-index="' + index + '"' + (met ? " checked" : "") +
        ' aria-label="标记验收条件">' +
        "<span>" + escapeHtml(criterionTitle(item, index)) +
        (item && item.verification_id ? '<small>核验 ' + escapeHtml(item.verification_id) + '</small>' : "") +
        "</span></li>";
    }).join("") : '<li class="criteria-item"><span>暂无验收条件</span></li>';
    var allCriteriaMet = criteria.length > 0 && criteria.every(function (item) { return item && item.met; });
    var reviewButtons = "";
    if (node.status === "waiting_review") {
      reviewButtons = '<div class="review-actions">' +
        '<button id="approve-review-button" class="button button-primary" type="button">批准</button>' +
        '<button id="reject-review-button" class="button button-quiet" type="button">退回</button></div>';
    } else if (readyForReview(node)) {
      reviewButtons = '<button id="submit-review-button" class="button button-primary complete-button" type="button">提交审核</button>';
    }
    var eventsHtml = events.length ? events.slice(0, 6).map(function (item) {
      return '<li class="event-item"><strong>' +
        escapeHtml(item.event_type || item.eventType || "event") + "</strong> · " +
        escapeHtml(item.reason || "未填写原因") + "</li>";
    }).join("") : '<li class="event-item">暂无事件</li>';
    var evidenceHtml = evidence.length ? evidence.slice(0, 6).map(function (item) {
      return '<li class="evidence-item"><strong>' +
        escapeHtml(item.title || item.evidence_type || "证据") + "</strong>" +
        (item.uri ? " · " + escapeHtml(item.uri) : "") + "</li>";
    }).join("") : '<li class="evidence-item">暂无证据</li>';
    $("detail-content").innerHTML =
      '<div class="detail-type">' + escapeHtml(typeLabel(node.nodeType)) + "</div>" +
      '<p class="detail-description">' + escapeHtml(node.description || "暂无描述") + "</p>" +
      '<div class="detail-meter"><div class="detail-meter-fill" style="width:' +
      percent(node.progress) + ";background:" + progressColor(node.progress) + '"></div></div>' +
      '<div class="detail-meter-row"><span>' + escapeHtml(statusLabel(node.status)) +
      '</span><strong>' + percent(node.progress) + "</strong></div>" +
      '<div class="detail-grid">' +
      detailField("优先级", node.priority) + detailField("置信度", percent(node.confidence)) +
      detailField("进度模式", node.progressMode) + detailField("版本", node.version) +
      "</div>" +
      reviewQueueHtml() +
      '<section class="detail-section"><h3>验收条件</h3><ul id="criteria-list" class="criteria-list">' +
      criteriaHtml + "</ul>" +
      (allCriteriaMet ? reviewButtons : "") +
      "</section>" +
      '<section class="detail-section"><h3>证据</h3><ul class="evidence-list">' +
      evidenceHtml + "</ul><button id=\"add-evidence-button\" class=\"button button-quiet detail-section-button\" type=\"button\">添加证据</button></section>" +
      lifecycleHtml(node) +
      '<section class="detail-section"><h3>最近事件</h3><ul class="event-list">' +
      eventsHtml + "</ul>" +
      (batchId ? '<button id="rollback-batch-button" class="button button-quiet detail-section-button" type="button">回滚最近批次</button>' : "") +
      "</section>";
    document.querySelectorAll("#criteria-list input[data-criterion-index]").forEach(function (input) {
      input.addEventListener("change", function (event) {
        toggleCriterion(node, Number(event.target.dataset.criterionIndex), event.target.checked);
      });
    });
    attachReviewQueueHandlers();
    if ($("submit-review-button")) {
      $("submit-review-button").addEventListener("click", function () {
        openConfirm(
          "提交审核",
          "该目标会进入待审核状态，后续需要人工批准或退回。",
          function () { requestSubmitForReview(node); },
          "提交"
        );
      });
    }
    if ($("approve-review-button")) {
      $("approve-review-button").addEventListener("click", function () {
        openConfirm(
          "批准审核",
          "批准后目标会被标记为已完成。",
          function () { requestApproveReview(node); },
          "批准"
        );
      });
    }
    if ($("reject-review-button")) {
      $("reject-review-button").addEventListener("click", function () {
        openConfirm(
          "退回审核",
          "退回后目标会回到进行中，保留已有证据和验收记录。",
          function () { requestRejectReview(node); },
          "退回"
        );
      });
    }
    if ($("verify-evidence-button")) {
      $("verify-evidence-button").addEventListener("click", function () { openVerifyEvidenceDialog(node); });
    }
    document.querySelectorAll(".lifecycle-apply[data-verification-id]").forEach(function (button) {
      button.addEventListener("click", function () {
        requestApplyVerification(node, button.dataset.verificationId);
      });
    });
    $("add-evidence-button").addEventListener("click", function () { openEvidenceDialog(node); });
    if ($("rollback-batch-button")) {
      $("rollback-batch-button").addEventListener("click", function () {
        openConfirm(
          "回滚最近批次",
          "回滚会还原该批次的目标变更，并在审计日志中留下回滚事件。",
          function () { requestRollback(node, false); },
          "继续回滚"
        );
      });
    }
  }

  function computeOverviewLayout(nodes, edges) {
    var nodeById = {};
    var rank = {};
    var order = {};
    nodes.forEach(function (node, index) {
      nodeById[node.id] = node;
      rank[node.id] = 0;
      order[node.id] = index;
    });
    var hierarchyEdges = (edges || []).filter(function (edge) {
      return edge.edge_type === "decomposes_to";
    });
    hierarchyEdges.forEach(function (edge) {
      var sourceId = edge.source_id || edge.sourceId;
      var targetId = edge.target_id || edge.targetId;
      if (!nodeById[sourceId] || !nodeById[targetId]) return;
    });
    for (var pass = 0; pass < nodes.length; pass += 1) {
      var changed = false;
      hierarchyEdges.forEach(function (edge) {
        var sourceId = edge.source_id || edge.sourceId;
        var targetId = edge.target_id || edge.targetId;
        if (rank[targetId] < rank[sourceId] + 1) {
          rank[targetId] = rank[sourceId] + 1;
          changed = true;
        }
      });
      if (!changed) break;
    }
    var columns = {};
    nodes.forEach(function (node) {
      var column = rank[node.id] || 0;
      (columns[column] || (columns[column] = [])).push(node);
    });
    var maxColumn = 0;
    Object.keys(columns).forEach(function (key) {
      columns[key].sort(function (left, right) { return order[left.id] - order[right.id]; });
      maxColumn = Math.max(maxColumn, columns[key].length);
    });
    var columnGap = nodes.length > 500 ? 190 : 220;
    var rowGap = nodes.length > 500 ? 46 : 58;
    var marginX = 72;
    var marginY = 42;
    var width = Math.max(900, marginX * 2 + Math.max(0, Object.keys(columns).length - 1) * columnGap);
    var height = Math.max(260, marginY * 2 + Math.max(0, maxColumn - 1) * rowGap);
    var positions = {};
    Object.keys(columns).forEach(function (key) {
      var column = columns[key];
      var x = marginX + Number(key) * columnGap;
      column.forEach(function (node, index) {
        var y = marginY + index * rowGap;
        positions[node.id] = { x: x, y: y };
      });
    });
    return { positions: positions, width: width, height: height };
  }

  function computeOverviewLayoutInWorker(nodes, edges) {
    if (typeof Worker === "undefined" || typeof Blob === "undefined" || typeof URL === "undefined") {
      return Promise.resolve(computeOverviewLayout(nodes, edges));
    }
    var source = "(" + computeOverviewLayout.toString() + ")\n" +
      "self.onmessage=function(event){self.postMessage(computeOverviewLayout(event.data.nodes,event.data.edges));};";
    var worker = new Worker(URL.createObjectURL(new Blob([source], { type: "text/javascript" })));
    return new Promise(function (resolve) {
      var settled = false;
      var finish = function (layout) {
        if (settled) return;
        settled = true;
        worker.terminate();
        resolve(layout);
      };
      worker.onmessage = function (event) { finish(event.data); };
      worker.onerror = function () { finish(computeOverviewLayout(nodes, edges)); };
      worker.postMessage({ nodes: nodes, edges: edges });
    });
  }

  function renderOverview() {
    var root = $("overview-content");
    var payload = state.overview;
    var requestId = state.overviewRequestId + 1;
    state.overviewRequestId = requestId;
    while (root.firstChild) root.removeChild(root.firstChild);
    if (!payload || !payload.nodes || !payload.nodes.length) {
      $("overview-empty").hidden = false;
      return Promise.resolve();
    }
    $("overview-empty").hidden = true;
    var nodes = payload.nodes.map(normalizeNode);
    var edges = payload.edges || [];
    var layoutPromise = nodes.length > 500 ?
      computeOverviewLayoutInWorker(nodes, edges) :
      Promise.resolve(computeOverviewLayout(nodes, edges));
    return layoutPromise.then(function (layout) {
      if (requestId !== state.overviewRequestId) return;
      var positions = layout.positions;
      var overviewSvg = $("overview-svg");
      overviewSvg.setAttribute("viewBox", "0 0 " + layout.width + " " + layout.height);
      overviewSvg.setAttribute("width", layout.width);
      overviewSvg.setAttribute("height", layout.height);
      var fragment = document.createDocumentFragment();
      (edges || []).forEach(function (edge) {
        var source = positions[edge.source_id || edge.sourceId];
        var target = positions[edge.target_id || edge.targetId];
        if (!source || !target) return;
        var edgeClass = "overview-edge";
        if (edge.edge_type === "depends_on") edgeClass += " overview-edge-dependency";
        if (edge.edge_type === "blocks") edgeClass += " overview-edge-block";
        fragment.appendChild(svg("line", {
          "class": edgeClass, x1: source.x, y1: source.y, x2: target.x, y2: target.y
        }));
      });
      var focusId = state.focus && state.focus.focus && state.focus.focus.id;
      var directIds = {};
      (state.focus && state.focus.children || []).forEach(function (node) { directIds[node.id] = true; });
      var compact = nodes.length > 500;
      nodes.forEach(function (node) {
        var position = positions[node.id];
        if (!position) return;
        var groupClass = "overview-node";
        if (node.id === focusId) groupClass += " focused";
        if (directIds[node.id]) groupClass += " direct";
        var group = svg("g", {
          "class": groupClass, "data-node-id": node.id, tabindex: "0", role: "button",
          "aria-label": node.title + " " + percent(node.progress)
        });
        group.appendChild(svg("circle", {
          cx: position.x, cy: position.y, r: node.id === focusId ? 13 : compact ? 7 : 9,
          fill: progressColor(node.progress)
        }));
        var title = svg("title", {});
        title.textContent = node.title + " · " + percent(node.progress);
        group.appendChild(title);
        if (!compact) {
          var label = svg("text", {
            x: position.x, y: position.y + 27, "text-anchor": "middle"
          });
          label.textContent = shortTitle(node.title, 18);
          group.appendChild(label);
        }
        group.addEventListener("click", function () { focusNode(node.id, node.id !== focusId); });
        group.addEventListener("contextmenu", function (event) {
          event.preventDefault();
          openNodeMenu(node, event.clientX, event.clientY);
        });
        group.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            group.click();
          }
        });
        fragment.appendChild(group);
      });
      root.appendChild(fragment);
    });
  }

  function detailField(label, value) {
    return '<div class="detail-field"><span>' + escapeHtml(label) +
      "</span><strong>" + escapeHtml(value) + "</strong></div>";
  }

  function populateProjects() {
    var select = $("project-select");
    select.innerHTML = "";
    if (!state.projects.length) {
      var option = document.createElement("option");
      option.value = "";
      option.textContent = "暂无项目";
      select.appendChild(option);
      $("empty-state").hidden = false;
      $("loading-state").hidden = true;
      return;
    }
    state.projects.forEach(function (project) {
      var option = document.createElement("option");
      option.value = project.id;
      option.textContent = project.name;
      option.selected = state.project && state.project.id === project.id;
      select.appendChild(option);
    });
  }

  function loadProjects(selectId) {
    setLoading(true);
    return api("/api/goals/projects").then(function (payload) {
      state.projects = (payload.projects || []).map(normalizeProject);
      state.project = state.projects.find(function (project) {
        return project.id === selectId;
      }) || state.projects[0] || null;
      state.lastEventId = null;
      populateProjects();
      if (!state.project) {
        setStatus("请创建第一个项目", false);
        setLoading(false);
        stopEventStream();
        return;
      }
      return loadFocus(state.project.rootNodeId).then(startEventStream);
    }).catch(function (error) {
      setLoading(false);
      showError(error);
    });
  }

  function toggleProjectForm(show) {
    $("project-form").hidden = !show;
    if (show) $("project-name").focus();
  }

  function createProject() {
    var name = $("project-name").value.trim();
    var reason = $("project-reason").value.trim();
    if (!name || !reason) {
      setStatus("项目名称和原因不能为空", true);
      return;
    }
    setStatus("正在创建项目...", false);
    api("/api/goals/projects", {
      method: "POST",
      body: {
        name: name,
        description: $("project-description").value.trim(),
        reason: reason,
        created_by: "user",
        actor_type: "user"
      }
    }).then(function (payload) {
      toggleProjectForm(false);
      var project = normalizeProject(payload.project);
      state.backStack = [];
      state.forwardStack = [];
      return loadProjects(project.id);
    }).catch(showError);
  }

  $("project-select").addEventListener("change", function (event) {
    state.project = state.projects.find(function (project) {
      return project.id === event.target.value;
    }) || null;
    state.backStack = [];
    state.forwardStack = [];
    state.selected = null;
    state.lastEventId = null;
    if (state.project) {
      loadFocus(state.project.rootNodeId).then(startEventStream);
    } else {
      stopEventStream();
    }
  });
  $("refresh-button").addEventListener("click", function () {
    loadProjects(state.project && state.project.id);
  });
  $("back-button").addEventListener("click", navigateBack);
  $("forward-button").addEventListener("click", navigateForward);
  $("undo-button").addEventListener("click", openUndoDialog);
  $("redo-button").addEventListener("click", openRedoDialog);
  document.querySelectorAll("#node-menu [data-menu-action]").forEach(function (button) {
    button.addEventListener("click", function () {
      handleMenuAction(button.dataset.menuAction);
    });
  });
  document.addEventListener("click", function (event) {
    if (!$("node-menu").contains(event.target)) closeNodeMenu();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeNodeMenu();
      closeDialog();
    }
  });
  $("dialog-close-button").addEventListener("click", closeDialog);
  $("dialog-cancel-button").addEventListener("click", closeDialog);
  $("goal-dialog").addEventListener("click", function (event) {
    if (event.target === $("goal-dialog")) closeDialog();
  });
  $("dialog-confirm-button").addEventListener("click", function () {
    var action = state.dialogAction;
    state.dialogAction = null;
    if (action) action();
  });
  $("edit-detail-button").addEventListener("click", function () {
    if (!state.selected) return;
    state.editing = true;
    renderDetail(state.selected);
    $("detail-edit-title").focus();
  });
  $("add-child-button").addEventListener("click", function () {
    openCreateChildDialog(currentCreateParent());
  });
  $("close-detail-button").addEventListener("click", function () {
    state.selected = null;
    state.editing = false;
    renderDetail(null);
  });
  $("new-project-button").addEventListener("click", function () { toggleProjectForm(true); });
  $("empty-create-button").addEventListener("click", function () { toggleProjectForm(true); });
  $("cancel-project-button").addEventListener("click", function () { toggleProjectForm(false); });
  $("create-project-button").addEventListener("click", createProject);
  $("parents-mode-button").addEventListener("click", function () {
    state.overviewMode = "parents_only";
    $("parents-mode-button").classList.add("active");
    $("dependencies-mode-button").classList.remove("active");
    loadOverview();
  });
  $("dependencies-mode-button").addEventListener("click", function () {
    state.overviewMode = "dependencies";
    $("dependencies-mode-button").classList.add("active");
    $("parents-mode-button").classList.remove("active");
    loadOverview();
  });
  $("radial-wrap").addEventListener("wheel", function (event) {
    event.preventDefault();
    state.zoom = clamp(state.zoom + (event.deltaY < 0 ? .05 : -.05), .82, 1.18);
    renderRadial();
  }, { passive: false });
  window.addEventListener("popstate", function () {
    var nodeId = decodeURIComponent(window.location.hash.slice(1));
    if (nodeId) focusNode(nodeId, false);
  });

  window.addEventListener("beforeunload", stopEventStream);

  loadProjects();
}());
