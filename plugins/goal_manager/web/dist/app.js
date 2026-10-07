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
    menuPreviousFocus: null,
    dialogAction: null,
    dialogPreviousFocus: null,
    history: null,
    reviewQueue: []
    ,overviewPan: { x: 0, y: 0 }
    ,overviewScale: 1
    ,focusPan: { x: 0, y: 0 }
    ,overviewPanDrag: null
    ,focusPanDrag: null
    ,suppressOverviewClick: false
    ,suppressRadialClick: false
    ,touchPointers: { overview: {}, focus: {} }
    ,touchGestures: { overview: null, focus: null }
  };
  var projectRequestId = 0;
  var detailRequestId = 0;
  var eventStreamGeneration = 0;
  var $ = function (id) { return document.getElementById(id); };
  var svgNs = "http://www.w3.org/2000/svg";
  function currentProjectValid(projectId) {
    return Boolean(state.project && state.project.id === projectId);
  }
  function currentProjectRequestValid(projectId, requestId) {
    return currentProjectValid(projectId) && requestId === projectRequestId;
  }

  function loadReviewToken() {
    var input = $("review-token-input");
    if (input) input.value = window.sessionStorage.getItem("voidcube_review_token") || "";
  }

  function saveReviewToken() {
    var input = $("review-token-input");
    var value = input ? input.value.trim() : "";
    if (value) window.sessionStorage.setItem("voidcube_review_token", value);
    else window.sessionStorage.removeItem("voidcube_review_token");
  }

  function revokeReviewSession() {
    var token = window.sessionStorage.getItem("voidcube_review_token");
    if (!token) return Promise.resolve();
    return fetch("/ui/review-session", {
      method: "DELETE",
      headers: { "X-VoidCube-Review-Token": token }
    }).catch(function () {}).then(function () {
      window.sessionStorage.removeItem("voidcube_review_token");
    });
  }

  function exchangeReviewSession() {
    var token = window.sessionStorage.getItem("voidcube_review_token");
    if (!token) return Promise.resolve(false);
    return fetch("/ui/review-session", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-VoidCube-Review-Token": token
      }
    }).then(function (response) {
      if (!response.ok) {
        window.sessionStorage.removeItem("voidcube_review_token");
        return false;
      }
      return response.json().then(function (payload) {
        if (payload && payload.review_token) {
          window.sessionStorage.setItem("voidcube_review_token", payload.review_token);
          return true;
        }
        return false;
      });
    }).catch(function () { return false; });
  }

  function reviewSessionError() {
    return new Error("审核会话不可用，请重新输入审核凭证");
  }

  function api(path, options) {
    options = options || {};
    saveReviewToken();
    var headers = { Accept: "application/json" };
    var reviewToken = window.sessionStorage.getItem("voidcube_review_token");
    if (reviewToken) headers["X-VoidCube-Review-Token"] = reviewToken;
    if (options.body) headers["Content-Type"] = "application/json";
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timedOut = false;
    var timeoutMs = Number(options.timeout);
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) timeoutMs = 30000;
    var timeoutId = controller ? window.setTimeout(function () {
      timedOut = true;
      controller.abort();
    }, timeoutMs) : null;
    var requestOptions = {
      method: options.method || "GET",
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined
    };
    if (controller) requestOptions.signal = controller.signal;
    return fetch(serviceUrl + path, requestOptions).then(function (response) {
      return response.text().then(function (text) {
        var payload = {};
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (_error) {
            var invalidPayload = new Error("Goal Service 返回了无效响应");
            invalidPayload.status = response.status;
            invalidPayload.payload = {};
            throw invalidPayload;
          }
        }
        if (response.ok && (!payload || typeof payload !== "object" || Array.isArray(payload))) {
          var invalidSuccess = new Error("Goal Service 返回了无效响应");
          invalidSuccess.status = response.status;
          invalidSuccess.payload = {};
          throw invalidSuccess;
        }
        if (!response.ok) {
          var error = new Error(payload.detail || "Goal Service 请求失败");
          error.payload = payload;
          error.status = response.status;
          throw error;
        }
        return payload;
      });
    }).catch(function (error) {
      if (timedOut) {
        var timeoutError = new Error("Goal Service 请求超时，请稍后重试");
        timeoutError.code = "timeout";
        throw timeoutError;
      }
      if (error && error.status != null) throw error;
      var networkError = new Error("Goal Service 不可用");
      networkError.code = "network_error";
      networkError.cause = error;
      throw networkError;
    }).finally(function () {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
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
    $("loading-state").setAttribute("aria-busy", loading ? "true" : "false");
    $("refresh-button").disabled = loading;
    $("refresh-button").setAttribute("aria-busy", loading ? "true" : "false");
    updateHistoryButtons();
  }

  function updateHistoryButtons() {
    var history = state.history || {};
    var disabled = state.loading || !state.project;
    $("undo-button").disabled = disabled || !history.can_undo;
    $("redo-button").disabled = disabled || !history.can_redo;
  }

  function loadHistory(expectedProjectId) {
    if (!state.project) {
      state.history = null;
      updateHistoryButtons();
      return Promise.resolve();
    }
    var projectId = expectedProjectId || state.project.id;
    var requestId = projectRequestId;
    return api("/api/goals/projects/" + encodeURIComponent(projectId) + "/history")
      .then(function (payload) {
        if (!currentProjectRequestValid(projectId, requestId)) return null;
        state.history = payload;
        updateHistoryButtons();
      })
      .catch(function (error) {
        if (!currentProjectRequestValid(projectId, requestId)) return null;
        state.history = null;
        updateHistoryButtons();
        return reportProjectError(projectId, error.message || "历史状态加载失败");
      });
  }

  function showError(error) {
    var message = error && error.message ? error.message : "Goal Service 不可用";
    if (error && error.status === 409 && error.payload && error.payload.latest) {
      message = "目标已被更新，已刷新当前数据，请重新检查后重试";
      var latestId = error.payload.latest.id;
      if (latestId) {
        var projectId = state.project && state.project.id;
        loadNodeDetail(latestId).catch(function () {
          if (!currentProjectValid(projectId)) return null;
          return null;
        });
      }
    }
    setStatus(message, true);
    if (message === "审核会话不可用，请重新输入审核凭证") {
      var reviewInput = $("review-token-input");
      if (reviewInput) {
        reviewInput.value = "";
        reviewInput.focus();
      }
    }
    $("loading-state").textContent = message === "Goal Service 不可用" ?
      "无法连接 Goal Service，请确认 6003 服务正在运行。" : message;
    $("loading-state").hidden = false;
  }

  function reportProjectError(projectId, message) {
    if (!currentProjectValid(projectId)) return null;
    setStatus(message, true);
    $("loading-state").textContent = message;
    $("loading-state").hidden = false;
    return null;
  }

  function showProjectError(error, projectId, requestId) {
    if (projectId == null && requestId == null) {
      if (state.project) return null;
    } else if (projectId == null) {
      if (requestId != null && requestId !== projectRequestId) return null;
    } else if (requestId == null ? !currentProjectValid(projectId) : !currentProjectRequestValid(projectId, requestId)) {
      return null;
    }
    showError(error);
    return null;
  }

  function stopEventStream() {
    eventStreamGeneration += 1;
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
    var projectId = state.project.id;
    var streamGeneration = eventStreamGeneration;
    var path = "/api/goals/events?project_id=" + encodeURIComponent(projectId);
    if (state.lastEventId) path += "&after=" + encodeURIComponent(state.lastEventId);
    api(path).then(function (payload) {
      if (!currentProjectValid(projectId) || streamGeneration !== eventStreamGeneration) return;
      var events = payload.events || [];
      if (!events.length) return;
      state.lastEventId = events[events.length - 1].id || state.lastEventId;
      var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
      setStatus("轮询发现目标更新，正在刷新...", false);
      if (focusedId) loadFocus(focusedId);
      else loadProjects(projectId);
    }).catch(function (error) {
      if (!currentProjectValid(projectId) || streamGeneration !== eventStreamGeneration) return;
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
    var projectId = state.project.id;
    var streamGeneration = eventStreamGeneration;
    var activeStream = function (source) {
      return currentProjectValid(projectId) && streamGeneration === eventStreamGeneration &&
        (!source || state.eventSource === source);
    };
    var open = function () {
      if (!activeStream()) return;
      var query = "?project_id=" + encodeURIComponent(projectId);
      if (state.lastEventId) query += "&after=" + encodeURIComponent(state.lastEventId);
      var source = new EventSource(
        serviceUrl + "/api/goals/projects/" + encodeURIComponent(projectId) + "/events" + query
      );
      state.eventSource = source;
      source.onopen = function () {
        if (!activeStream(source)) {
          source.close();
          if (state.eventSource === source) state.eventSource = null;
          return;
        }
        if (state.pollTimer) {
          window.clearInterval(state.pollTimer);
          state.pollTimer = null;
        }
        setStatus("实时更新已连接", false);
      };
      source.onmessage = function (message) {
        if (!activeStream(source)) return;
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
        else loadProjects(projectId);
      };
      source.onerror = function () {
        source.close();
        if (state.eventSource === source) state.eventSource = null;
        if (!activeStream()) return;
        startPollingFallback();
        if (activeStream() && !state.eventSource && !state.streamRetryTimer) {
          state.streamRetryTimer = window.setTimeout(function () {
            state.streamRetryTimer = null;
            if (activeStream() && !state.eventSource) startEventStream();
          }, 3000);
        }
      };
    };
    if (state.lastEventId) {
      open();
      return;
    }
    api("/api/goals/events/latest?project_id=" + encodeURIComponent(projectId))
      .then(function (payload) {
        if (!activeStream()) return;
        state.lastEventId = payload.event_id || null;
        open();
      }).catch(function () {
        if (activeStream()) open();
      });
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

  function layoutStorageKey(projectId) {
    return "voidcube_goal_layout:" + String(projectId || "");
  }

  function restoreLayoutState(projectId) {
    state.overviewPan = { x: 0, y: 0 };
    state.overviewScale = 1;
    state.focusPan = { x: 0, y: 0 };
    try {
      var saved = JSON.parse(window.localStorage.getItem(layoutStorageKey(projectId)) || "null");
      if (saved && saved.pan) state.overviewPan = { x: Number(saved.pan.x) || 0, y: Number(saved.pan.y) || 0 };
      if (saved && Number.isFinite(Number(saved.scale))) state.overviewScale = clamp(Number(saved.scale), .65, 1.8);
    } catch (_error) {}
  }

  function persistLayoutState() {
    if (!state.project) return;
    try {
      window.localStorage.setItem(layoutStorageKey(state.project.id), JSON.stringify({
        pan: state.overviewPan,
        scale: state.overviewScale
      }));
    } catch (_error) {}
  }

  function svgClientDelta(svgElement, startClient, event, divisor) {
    var rect = svgElement.getBoundingClientRect();
    var viewBox = svgElement.viewBox && svgElement.viewBox.baseVal;
    var scale = Number(divisor) || 1;
    if (!viewBox || !rect.width || !rect.height) {
      return { x: (event.clientX - startClient.x) / scale, y: (event.clientY - startClient.y) / scale };
    }
    return {
      x: (event.clientX - startClient.x) * viewBox.width / rect.width / scale,
      y: (event.clientY - startClient.y) * viewBox.height / rect.height / scale
    };
  }

  function svgPixelDelta(svgElement, deltaX, deltaY, divisor) {
    var rect = svgElement.getBoundingClientRect();
    var viewBox = svgElement.viewBox && svgElement.viewBox.baseVal;
    var scale = Number(divisor) || 1;
    if (!viewBox || !rect.width || !rect.height) {
      return { x: deltaX / scale, y: deltaY / scale };
    }
    return {
      x: deltaX * viewBox.width / rect.width / scale,
      y: deltaY * viewBox.height / rect.height / scale
    };
  }

  function surfaceSvg(surface) {
    return $(surface === "overview" ? "overview-svg" : "radial-svg");
  }

  function surfaceScale(surface) {
    return surface === "overview" ? state.overviewScale : state.zoom;
  }

  function capturePointer(svgElement, pointerId) {
    try { svgElement.setPointerCapture(pointerId); } catch (_error) {}
  }

  function releasePointer(svgElement, pointerId) {
    try {
      if (svgElement.hasPointerCapture(pointerId)) svgElement.releasePointerCapture(pointerId);
    } catch (_error) {}
  }

  function setSurfaceScale(surface, value) {
    if (surface === "overview") {
      setOverviewScale(value);
      return;
    }
    state.zoom = clamp(Number(value) || 1, .65, 1.8);
    applyFocusScale();
  }

  function applyFocusScale() {
    $("radial-svg").style.transform = "scale(" + state.zoom + ")";
    $("zoom-label").textContent = Math.round(state.zoom * 100) + "%";
  }

  function touchPointerCount(surface) {
    return Object.keys(state.touchPointers[surface]).length;
  }

  function touchPointerList(surface) {
    return Object.keys(state.touchPointers[surface]).map(function (id) {
      return state.touchPointers[surface][id];
    });
  }

  function touchMidpoint(first, second) {
    return { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
  }

  function touchDistance(first, second) {
    return Math.max(1, Math.hypot(first.x - second.x, first.y - second.y));
  }

  function beginTouchPointer(surface, event) {
    var svgElement = surfaceSvg(surface);
    var pointers = state.touchPointers[surface];
    pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
    capturePointer(svgElement, event.pointerId);
    if (touchPointerCount(surface) !== 2) return;
    var points = touchPointerList(surface);
    var midpoint = touchMidpoint(points[0], points[1]);
    state.touchGestures[surface] = {
      startMidpoint: midpoint,
      startDistance: touchDistance(points[0], points[1]),
      basePan: surface === "overview" ? { x: state.overviewPan.x, y: state.overviewPan.y } : { x: state.focusPan.x, y: state.focusPan.y },
      baseScale: surfaceScale(surface),
      moved: false
    };
  }

  function moveTouchPointer(surface, event) {
    var pointers = state.touchPointers[surface];
    if (!pointers[event.pointerId]) return;
    pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
    var gesture = state.touchGestures[surface];
    if (!gesture || touchPointerCount(surface) < 2) return;
    var points = touchPointerList(surface);
    var midpoint = touchMidpoint(points[0], points[1]);
    var distance = touchDistance(points[0], points[1]);
    var svgElement = surfaceSvg(surface);
    var panDelta = svgPixelDelta(
      svgElement,
      midpoint.x - gesture.startMidpoint.x,
      midpoint.y - gesture.startMidpoint.y,
      gesture.baseScale
    );
    var scale = clamp(gesture.baseScale * distance / gesture.startDistance, .65, 1.8);
    if (Math.abs(panDelta.x) + Math.abs(panDelta.y) > 3 || Math.abs(scale - gesture.baseScale) > .01) {
      gesture.moved = true;
    }
    if (surface === "overview") {
      state.overviewPan = { x: gesture.basePan.x + panDelta.x, y: gesture.basePan.y + panDelta.y };
      state.overviewScale = scale;
      applyOverviewTransform();
    } else {
      state.focusPan = { x: gesture.basePan.x + panDelta.x, y: gesture.basePan.y + panDelta.y };
      state.zoom = scale;
      applyFocusPan();
      applyFocusScale();
    }
  }

  function endTouchPointer(surface, event) {
    var svgElement = surfaceSvg(surface);
    var gesture = state.touchGestures[surface];
    releasePointer(svgElement, event.pointerId);
    delete state.touchPointers[surface][event.pointerId];
    if (gesture && gesture.moved) {
      if (surface === "overview") {
        state.suppressOverviewClick = true;
        persistLayoutState();
      } else {
        state.suppressRadialClick = true;
      }
    }
    if (!touchPointerCount(surface)) state.touchGestures[surface] = null;
  }

  function applyOverviewTransform() {
    var root = $("overview-content");
    if (!root) return;
    root.setAttribute("transform", "translate(" + state.overviewPan.x + " " + state.overviewPan.y + ") scale(" + state.overviewScale + ")");
    $("overview-zoom-label").textContent = Math.round(state.overviewScale * 100) + "%";
  }

  function applyFocusPan() {
    var root = $("radial-content");
    if (root) root.setAttribute("transform", "translate(" + state.focusPan.x + " " + state.focusPan.y + ")");
  }

  function shortTitle(title, max) {
    var chars = Array.from(String(title || ""));
    return chars.length > max ? chars.slice(0, max - 1).join("") + "…" : chars.join("");
  }

  function svgPointToClient(svgElement, x, y) {
    var rect = svgElement.getBoundingClientRect();
    var viewBox = svgElement.viewBox && svgElement.viewBox.baseVal;
    if (!viewBox || !viewBox.width || !viewBox.height) {
      return { x: rect.left + x, y: rect.top + y };
    }
    return {
      x: rect.left + (x - viewBox.x) * rect.width / viewBox.width,
      y: rect.top + (y - viewBox.y) * rect.height / viewBox.height
    };
  }

  function openNodeMenuAtSvgPoint(node, svgElement, x, y) {
    var point = svgPointToClient(svgElement, x, y);
    openNodeMenu(node, point.x, point.y);
  }

  function makeNodeGroup(node, x, y, radius, focused) {
    var circumference = 2 * Math.PI * (radius + 10);
    var group = svg("g", {
      "class": "node-group",
      "data-node-id": node.id,
      tabindex: "0",
      role: "button",
      "aria-label": node.title + "，" + statusLabel(node.status) + "，完成度 " + percent(node.progress),
      "aria-keyshortcuts": "Enter Space Shift+F10"
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
      if (state.suppressRadialClick) {
        state.suppressRadialClick = false;
        return;
      }
      if (focused) selectNode(node.id);
      else focusNode(node.id, true);
    });
    group.addEventListener("contextmenu", function (event) {
      event.preventDefault();
      openNodeMenu(node, event.clientX, event.clientY);
    });
    group.addEventListener("keydown", function (event) {
      if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
        event.preventDefault();
        openNodeMenuAtSvgPoint(node, $("radial-svg"), x, y);
        return;
      }
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
    applyFocusPan();
    applyFocusScale();
    var focus = state.focus && normalizeNode(state.focus.focus);
    if (!focus) {
      $("empty-state").hidden = false;
      $("focus-heading").textContent = "没有焦点目标";
      $("project-progress").textContent = "项目进度 --";
      $("breadcrumb").innerHTML = "";
      updateNavigationButtons();
      return;
    }
    $("empty-state").hidden = true;
    var children = (state.focus.children || []).map(normalizeNode).filter(Boolean);
    var cx = 450;
    var cy = 305;
    var radius = children.length > 30 ? 48 : 56;
    var orbit = children.length <= 12 ? 185 : 172;
    for (var starIndex = 0; starIndex < 34; starIndex += 1) {
      var starX = 34 + stableHash(String(starIndex) + focus.id) * 832;
      var starY = 24 + stableHash(focus.id + String(starIndex)) * 560;
      root.appendChild(svg("circle", {
        "class": "constellation-star",
        cx: starX,
        cy: starY,
        r: starIndex % 7 === 0 ? 1.8 : 1,
        opacity: .25 + stableHash(String(starIndex * 3) + focus.id) * .5
      }));
    }
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
    root.appendChild(makeNodeGroup(focus, cx, cy, 68, true));
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
    var projectId = state.project.id;
    var requestId = ++projectRequestId;
    setLoading(true);
    var path = "/api/goals/projects/" + encodeURIComponent(projectId) + "/focus";
    if (nodeId) path += "?node=" + encodeURIComponent(nodeId);
    return api(path).then(function (payload) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      state.focus = payload;
      setLoading(false);
      setStatus("目标图已更新", false);
      renderRadial();
      return Promise.all([
        loadOverview(projectId), loadHistory(projectId), loadProjectSummary(projectId), loadReviewQueue(projectId)
      ]).then(function () {
        if (!currentProjectRequestValid(projectId, requestId)) return null;
        renderDetail(state.selected);
      });
    }).catch(function (error) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      setLoading(false);
      showError(error);
      return null;
    });
  }

  function loadOverview(expectedProjectId) {
    if (!state.project) return Promise.resolve();
    var projectId = expectedProjectId || state.project.id;
    var requestId = projectRequestId;
    var path = "/api/goals/projects/" + encodeURIComponent(projectId) +
      "/overview?mode=" + encodeURIComponent(state.overviewMode);
    return api(path).then(function (payload) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      state.overview = payload;
      renderOverview(projectId, requestId);
    }).catch(function (error) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      return reportProjectError(projectId, error.message || "总览加载失败");
    });
  }

  function loadProjectSummary(expectedProjectId) {
    if (!state.project) return Promise.resolve();
    var projectId = expectedProjectId || state.project.id;
    var requestId = projectRequestId;
    return api("/api/goals/projects/" + encodeURIComponent(projectId)).then(function (payload) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      state.project = normalizeProject(payload);
      renderRadial();
    }).catch(function (error) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      return reportProjectError(projectId, error.message || "项目摘要加载失败");
    });
  }

  function focusNode(nodeId, pushHistory) {
    if (!state.project || !nodeId) return Promise.resolve(null);
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

  function updateNodeHash(nodeId) {
    var hash = nodeId ? "#" + encodeURIComponent(nodeId) : "";
    window.history.replaceState(nodeId ? { nodeId: nodeId } : {}, "", hash || window.location.pathname);
  }

  function readNodeHash() {
    var encoded = window.location.hash.slice(1);
    if (!encoded) return null;
    try {
      return decodeURIComponent(encoded) || null;
    } catch (_error) {
      updateNodeHash(null);
      return null;
    }
  }

  function selectNode(nodeId) {
    if (!state.project || !state.focus) return;
    var focus = normalizeNode(state.focus.focus);
    var child = (state.focus.children || []).map(normalizeNode).find(function (node) {
      return node && node.id === nodeId;
    });
    state.selected = focus && focus.id === nodeId ? focus : child || null;
    if (!state.selected || (state.selected.projectId && state.selected.projectId !== state.project.id)) {
      state.selected = null;
      return;
    }
    state.editing = false;
    updateNodeHash(state.selected.id);
    renderDetail(state.selected);
    if (state.selected) loadNodeDetail(state.selected.id);
  }

  function loadNodeDetail(nodeId) {
    var projectId = state.project && state.project.id;
    var requestId = ++detailRequestId;
    return Promise.all([
      api("/api/goals/nodes/" + encodeURIComponent(nodeId)),
      api("/api/goals/nodes/" + encodeURIComponent(nodeId) + "/lifecycle")
    ]).then(function (results) {
      if (!currentProjectValid(projectId) || requestId !== detailRequestId) return null;
      var node = normalizeNode(results[0]);
      if (!node || node.projectId !== projectId) {
        throw new Error("当前目标已不属于所选项目，请刷新后重试");
      }
      node.lifecycle = normalizeLifecycle(results[1]);
      if (state.selected && state.selected.id === nodeId) {
        state.selected = Object.assign(state.selected, node);
        renderDetail(state.selected);
      }
      return node;
    }).catch(function (error) {
      if (!currentProjectValid(projectId) || requestId !== detailRequestId) return null;
      return reportProjectError(projectId, error.message || "详情加载失败");
    });
  }

  function loadReviewQueue(expectedProjectId) {
    if (!state.project) {
      state.reviewQueue = [];
      return Promise.resolve();
    }
    var projectId = expectedProjectId || state.project.id;
    var requestId = projectRequestId;
    return api("/api/goals/projects/" + encodeURIComponent(projectId) + "/overview")
      .then(function (payload) {
        if (!currentProjectRequestValid(projectId, requestId)) return null;
        state.reviewQueue = (payload.nodes || []).map(normalizeNode).filter(function (node) {
          return node.status === "waiting_review";
        });
      }).catch(function (error) {
        if (currentProjectRequestValid(projectId, requestId)) state.reviewQueue = [];
        if (!currentProjectRequestValid(projectId, requestId)) return null;
        return reportProjectError(projectId, error.message || "待审核队列加载失败");
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
    var previousFocus = state.menuPreviousFocus;
    $("node-menu").hidden = true;
    state.menuNode = null;
    state.menuPreviousFocus = null;
    if (previousFocus && document.contains(previousFocus) && previousFocus !== document.activeElement) previousFocus.focus();
  }

  function closeDialog() {
    if ($("goal-dialog").hidden) {
      state.dialogAction = null;
      state.dialogPreviousFocus = null;
      return;
    }
    var previousFocus = state.dialogPreviousFocus;
    $("goal-dialog").hidden = true;
    $("goal-dialog").setAttribute("aria-hidden", "true");
    $("dialog-content").innerHTML = "";
    state.dialogAction = null;
    state.dialogPreviousFocus = null;
    if (previousFocus && document.contains(previousFocus) && previousFocus !== document.activeElement) previousFocus.focus();
  }

  function invalidatePendingInteraction() {
    state.dialogAction = null;
    state.menuNode = null;
    state.menuPreviousFocus = null;
    state.dialogPreviousFocus = null;
    closeNodeMenu();
    closeDialog();
    detailRequestId += 1;
  }

  function dialogFocusableElements() {
    return $("goal-dialog").querySelectorAll("button, input, select, textarea, [href], [tabindex]:not([tabindex=\"-1\"])");
  }

  function openDialog(title, content, action, confirmLabel) {
    state.dialogPreviousFocus = document.activeElement;
    closeNodeMenu();
    $("dialog-title").textContent = title;
    $("dialog-content").innerHTML = content;
    $("dialog-confirm-button").textContent = confirmLabel || "确认";
    state.dialogAction = action;
    $("goal-dialog").hidden = false;
    $("goal-dialog").setAttribute("aria-hidden", "false");
    var firstField = $("dialog-content").querySelector("input, select, textarea, button");
    (firstField || $("dialog-confirm-button")).focus();
  }

  function openConfirm(title, message, action, confirmLabel) {
    openDialog(
      title,
      '<p class="dialog-message">' + escapeHtml(message) + "</p>",
      action,
      confirmLabel
    );
  }

  function runDialogAction() {
    var action = state.dialogAction;
    if (!action || $("goal-dialog").hidden) return;
    state.dialogAction = null;
    action();
  }

  function freshNode(node) {
    var projectId = state.project && state.project.id;
    var requestId = ++detailRequestId;
    return api("/api/goals/nodes/" + encodeURIComponent(node.id))
      .then(function (payload) {
        if (!currentProjectValid(projectId) || requestId !== detailRequestId) return null;
        var fresh = normalizeNode(payload);
        if (fresh.projectId !== projectId) {
          throw new Error("当前目标已不属于所选项目，请刷新后重试");
        }
        return fresh;
      });
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
    var projectId = state.project && state.project.id;
    if (!projectId || (parent.projectId && parent.projectId !== projectId)) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var title = $("create-child-title").value.trim();
    var reason = $("create-child-reason").value.trim();
    if (!title || !reason) {
      setStatus("子目标标题和原因不能为空", true);
      return;
    }
    var requestId = projectRequestId;
    setStatus("正在创建子目标...", false);
    api("/api/goals/batch", {
      method: "POST",
      body: {
        project_id: projectId,
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
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("子目标已创建", false);
      return focusNode(parent.id, false).then(function () {
        state.selected = parent;
        state.editing = false;
        renderDetail(parent);
        return loadNodeDetail(parent.id);
      });
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function submitEvidence(node) {
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var reason = $("evidence-reason").value.trim();
    if (!reason) {
      setStatus("证据原因不能为空", true);
      return;
    }
    var requestId = projectRequestId;
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
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("证据已写入", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
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
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var reason = $("verify-reason").value.trim();
    var summary = $("verify-summary").value.trim();
    if (!reason || !summary) {
      setStatus("核验摘要和原因不能为空", true);
      return;
    }
    var requestId = projectRequestId;
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
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("核验记录已写入", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function requestRollback(node, confirmToken) {
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var batchId = latestBatchId(node);
    if (!batchId) {
      setStatus("该目标没有可回滚的批次", true);
      return;
    }
    requestRollbackBatch(batchId, confirmToken, node.id);
  }

  function requestRollbackBatch(batchId, confirmToken, nodeId) {
    var projectId = state.project && state.project.id;
    if (!projectId) return;
    var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
    var requestId = projectRequestId;
    api("/api/goals/rollback", {
      method: "POST",
      body: {
        batch_id: batchId,
        reason: "通过目标管理界面回滚批次",
        confirm_token: confirmToken || undefined,
        actor_type: "user"
      }
    }).then(function () {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("批次已回滚", false);
      if (focusedId && state.focus && state.focus.focus && state.focus.focus.id !== focusedId) return null;
      return refreshAfterNodeChange(nodeId);
    }).catch(function (error) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      if (error.payload && error.payload.requires_confirm) {
        openConfirm(
          "服务端确认回滚",
          "该批次包含较多变更，服务端要求再次确认后才会回滚。",
          function () { requestRollbackBatch(batchId, error.payload.confirm_token, nodeId); },
          "确认回滚"
        );
        return;
      }
      return showProjectError(error, projectId, requestId);
    });
  }

  function requestRedo(batchId) {
    var projectId = state.project && state.project.id;
    if (!projectId) return;
    var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
    var requestId = projectRequestId;
    api("/api/goals/redo", {
      method: "POST",
      body: {
        project_id: projectId,
        batch_id: batchId,
        reason: "通过目标管理界面重做批次",
        actor_type: "user"
      }
    }).then(function () {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("批次已重做", false);
      return focusedId ? loadFocus(focusedId) : loadProjects(projectId);
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
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
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var currentFocusId = state.focus && state.focus.focus && state.focus.focus.id;
    var requestId = projectRequestId;
    var query = "?reason=" + encodeURIComponent("通过目标管理界面删除节点") +
      "&actor_type=user";
    if (confirmToken) query += "&confirm_token=" + encodeURIComponent(confirmToken);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + query, {
      method: "DELETE"
    }).then(function () {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
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
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      if (error.payload && error.payload.requires_confirm) {
        openConfirm(
          "服务端确认删除",
          "这是受保护的危险操作，服务端要求确认后才会继续。",
          function () { requestDelete(node, error.payload.confirm_token); },
          "确认删除"
        );
        return;
      }
      return showProjectError(error, projectId, requestId);
    });
  }

  function beginNodeEdit(node) {
    var projectId = state.project && state.project.id;
    var requestId = detailRequestId;
    freshNode(node).then(function (fresh) {
      if (!fresh) return;
      state.selected = fresh;
      state.editing = true;
      renderDetail(fresh);
      $("detail-edit-title").focus();
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function blockNode(node) {
    var projectId = state.project && state.project.id;
    var requestId = detailRequestId;
    freshNode(node).then(function (fresh) {
      if (fresh) patchNode(fresh, { status: "blocked" }, "通过目标管理界面标记阻塞");
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function openNodeMenu(node, clientX, clientY) {
    if (!node || !state.project || (node.projectId && node.projectId !== state.project.id)) return;
    state.menuPreviousFocus = document.activeElement;
    state.menuNode = node;
    var menu = $("node-menu");
    menu.hidden = false;
    menu.style.left = Math.max(8, Math.min(clientX, window.innerWidth - 176)) + "px";
    menu.style.top = Math.max(8, Math.min(clientY, window.innerHeight - 220)) + "px";
    var firstItem = menu.querySelector("button");
    if (firstItem) firstItem.focus();
  }

  function nodeMenuFocusableElements() {
    return Array.prototype.slice.call($("node-menu").querySelectorAll("button:not([disabled])"));
  }

  function handleMenuAction(action) {
    var node = state.menuNode;
    closeNodeMenu();
    if (!node || !state.project || (node.projectId && node.projectId !== state.project.id)) return;
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
      var projectId = state.project && state.project.id;
      var requestId = detailRequestId;
      freshNode(node).then(function (fresh) { if (fresh) openEvidenceDialog(fresh); }).catch(function (error) {
        return showProjectError(error, projectId, requestId);
      });
    } else if (action === "rollback") {
      var rollbackProjectId = state.project && state.project.id;
      var rollbackRequestId = detailRequestId;
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
      }).catch(function (error) {
        return showProjectError(error, rollbackProjectId, rollbackRequestId);
      });
    } else if (action === "delete") {
      var deleteProjectId = state.project && state.project.id;
      var deleteRequestId = detailRequestId;
      freshNode(node).then(function (fresh) {
        if (fresh) {
          openConfirm(
            "删除目标节点",
            "节点将被软删除；如果它是项目根节点或包含子节点，服务端还会要求额外确认。",
            function () { requestDelete(fresh); },
            "继续删除"
          );
        }
      }).catch(function (error) {
        return showProjectError(error, deleteProjectId, deleteRequestId);
      });
    }
  }

  function refreshAfterNodeChange(nodeId) {
    var projectId = state.project && state.project.id;
    var focusedId = state.focus && state.focus.focus && state.focus.focus.id;
    var refresh = focusedId ? loadFocus(focusedId) : loadProjects(state.project && state.project.id);
    return refresh.then(function () {
      if (!currentProjectValid(projectId)) return null;
      if (state.selected && state.selected.id === nodeId) return loadNodeDetail(nodeId);
      return null;
    }).catch(function (error) { return showProjectError(error, projectId); });
  }

  function patchNode(node, patch, reason) {
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return Promise.resolve(null);
    }
    var requestId = projectRequestId;
    return api("/api/goals/nodes/" + encodeURIComponent(node.id), {
      method: "PATCH",
      body: {
        expected_version: node.version,
        patch: patch,
        reason: reason,
        actor_type: "user"
      }
    }).then(function (payload) {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      state.selected = normalizeNode(payload.node);
      state.editing = false;
      renderDetail(state.selected);
      setStatus("目标已更新", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) {
      if (currentProjectRequestValid(projectId, requestId)) renderDetail(state.selected);
      return showProjectError(error, projectId, requestId);
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
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var requestId = projectRequestId;
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
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      setStatus("核验结论已应用", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function requestSubmitForReview(node) {
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var requestId = projectRequestId;
    setStatus("正在提交审核...", false);
    api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/submit-for-review", {
      method: "POST",
      body: {
        expected_version: node.version,
        reason: "通过目标管理界面提交审核",
        actor_type: "user"
      }
    }).then(function () {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("目标已进入待审核", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function requestApproveReview(node) {
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var requestId = projectRequestId;
    saveReviewToken();
    setStatus("正在批准审核...", false);
    exchangeReviewSession().then(function (ready) {
      if (!ready) throw reviewSessionError();
      return api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/approve-review", {
      method: "POST",
      body: {
        expected_version: node.version,
        reason: "通过目标管理界面批准审核",
        actor_type: "user"
      }
    }); }).then(function () {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("审核已批准，目标完成", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function requestRejectReview(node) {
    var projectId = state.project && state.project.id;
    if (!projectId || node.projectId !== projectId) {
      setStatus("当前目标已不属于所选项目，请刷新后重试", true);
      return;
    }
    var requestId = projectRequestId;
    saveReviewToken();
    setStatus("正在退回审核...", false);
    exchangeReviewSession().then(function (ready) {
      if (!ready) throw reviewSessionError();
      return api("/api/goals/nodes/" + encodeURIComponent(node.id) + "/reject-review", {
      method: "POST",
      body: {
        expected_version: node.version,
        reason: "通过目标管理界面退回审核",
        actor_type: "user"
      }
    }); }).then(function () {
      if (!currentProjectRequestValid(projectId, requestId)) return null;
      closeDialog();
      setStatus("审核已退回", false);
      return refreshAfterNodeChange(node.id);
    }).catch(function (error) { return showProjectError(error, projectId, requestId); });
  }

  function renderDetail(node) {
    $("detail-title").textContent = node ? node.title : "选择一个目标";
    $("edit-detail-button").hidden = !node || state.editing;
    $("delete-detail-button").hidden = !node || state.editing;
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
        '<p class="detail-description">' + escapeHtml(node.description || "暂无描述") + "</p>" +
        '<section class="detail-section detail-summary"><div class="detail-summary-heading">' +
        '<span class="detail-type">' + escapeHtml(typeLabel(node.nodeType)) + "</span>" +
        '<strong class="detail-summary-status">' + escapeHtml(statusLabel(node.status)) + "</strong></div>" +
        '<div class="detail-meter"><div class="detail-meter-fill" style="width:' +
        percent(node.progress) + ";background:" + progressColor(node.progress) + '"></div></div>' +
        '<div class="detail-meter-row"><span>完成进度</span><strong>' + percent(node.progress) + "</strong></div>" +
        '<div class="detail-grid">' +
        detailField("优先级", node.priority) + detailField("置信度", percent(node.confidence)) +
        detailField("进度模式", node.progressMode) + detailField("版本", node.version) +
        "</div></section>" +
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

  function renderOverview(expectedProjectId, expectedRequestId) {
    var root = $("overview-content");
    var payload = state.overview;
    var requestId = state.overviewRequestId + 1;
    state.overviewRequestId = requestId;
    while (root.firstChild) root.removeChild(root.firstChild);
    if (expectedProjectId && !currentProjectRequestValid(expectedProjectId, expectedRequestId)) {
      return Promise.resolve();
    }
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
      if (expectedProjectId && !currentProjectRequestValid(expectedProjectId, expectedRequestId)) return;
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
          "class": edgeClass,
          "data-source-id": edge.source_id || edge.sourceId,
          "data-target-id": edge.target_id || edge.targetId,
          x1: source.x, y1: source.y, x2: target.x, y2: target.y
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
          "aria-label": node.title + "，" + statusLabel(node.status) + "，完成度 " + percent(node.progress),
          "aria-keyshortcuts": "Enter Space Shift+F10"
        });
        group.setAttribute("data-base-x", position.x);
        group.setAttribute("data-base-y", position.y);
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
        group.addEventListener("click", function () {
          if (state.suppressOverviewClick) {
            state.suppressOverviewClick = false;
            return;
          }
          focusNode(node.id, node.id !== focusId);
        });
        group.addEventListener("contextmenu", function (event) {
          event.preventDefault();
          openNodeMenu(node, event.clientX, event.clientY);
        });
        group.addEventListener("keydown", function (event) {
          if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
            event.preventDefault();
            openNodeMenuAtSvgPoint(node, $("overview-svg"), position.x, position.y);
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            group.click();
          }
        });
        fragment.appendChild(group);
      });
      root.appendChild(fragment);
      applyOverviewTransform();
    });
  }

  function detailField(label, value) {
    return '<div class="detail-field"><span>' + escapeHtml(label) +
      "</span><strong>" + escapeHtml(value) + "</strong></div>";
  }

  function setOverviewScale(value) {
    state.overviewScale = clamp(Number(value) || 1, .65, 1.8);
    persistLayoutState();
    applyOverviewTransform();
  }

  function populateProjects() {
    var select = $("project-select");
    select.innerHTML = "";
    if (!state.projects.length) {
      state.project = null;
      state.focus = null;
      state.selected = null;
      state.overview = null;
      state.history = null;
      state.reviewQueue = [];
      updateHistoryButtons();
      renderDetail(null);
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
    var requestId = ++projectRequestId;
    invalidatePendingInteraction();
    stopEventStream();
    setLoading(true);
    return api("/api/goals/projects").then(function (payload) {
      if (requestId !== projectRequestId) return null;
      state.projects = (payload.projects || []).map(normalizeProject);
      state.project = state.projects.find(function (project) {
        return project.id === selectId;
      }) || state.projects[0] || null;
      if (state.project) restoreLayoutState(state.project.id);
      state.lastEventId = null;
      populateProjects();
      if (!state.project) {
        state.focus = null;
        state.selected = null;
        state.overview = null;
        state.history = null;
        state.reviewQueue = [];
        updateHistoryButtons();
        setStatus("请创建第一个项目", false);
        setLoading(false);
        stopEventStream();
        renderRadial();
        renderOverview();
        renderDetail(null);
        return;
      }
      return loadFocus(state.project.rootNodeId).then(startEventStream);
    }).catch(function (error) {
      if (requestId !== projectRequestId) return null;
      setLoading(false);
      return showProjectError(error, state.project && state.project.id, requestId);
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
    var requestId = ++projectRequestId;
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
      if (requestId !== projectRequestId) return null;
      toggleProjectForm(false);
      var project = normalizeProject(payload.project);
      state.backStack = [];
      state.forwardStack = [];
      return loadProjects(project.id);
    }).catch(function (error) { return showProjectError(error, null, requestId); });
  }

  $("project-select").addEventListener("change", function (event) {
    invalidatePendingInteraction();
    stopEventStream();
    state.project = state.projects.find(function (project) {
      return project.id === event.target.value;
    }) || null;
    state.backStack = [];
    state.forwardStack = [];
    state.selected = null;
    state.lastEventId = null;
    if (state.project) restoreLayoutState(state.project.id);
    updateNodeHash(state.project && state.project.rootNodeId);
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
    if (!$('node-menu').hidden && event.key === "Tab") {
      var items = nodeMenuFocusableElements();
      if (items.length) {
        var first = items[0];
        var last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
      return;
    }
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
    runDialogAction();
  });
  $("goal-dialog").addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDialog();
      return;
    }
    if (event.key === "Tab") {
      var focusable = dialogFocusableElements();
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    if (event.key === "Enter" && !event.isComposing && event.target.tagName !== "TEXTAREA") {
      event.preventDefault();
      runDialogAction();
    }
  });
  $("edit-detail-button").addEventListener("click", function () {
    if (!state.selected) return;
    state.editing = true;
    renderDetail(state.selected);
    $("detail-edit-title").focus();
  });
  $("delete-detail-button").addEventListener("click", function () {
    if (!state.selected) return;
    var node = state.selected;
    openConfirm(
      "删除目标节点",
      "目标会从当前项目视图中移除，并保留在审计记录中。包含子目标的节点可能需要服务端再次确认。",
      function () { requestDelete(node); },
      "确认删除"
    );
  });
  $("add-child-button").addEventListener("click", function () {
    openCreateChildDialog(currentCreateParent());
  });
  $("close-detail-button").addEventListener("click", function () {
    state.selected = null;
    state.editing = false;
    updateNodeHash(state.focus && state.focus.focus && state.focus.focus.id);
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
  $("focus-reset-button").addEventListener("click", function () {
    state.zoom = 1;
    state.focusPan = { x: 0, y: 0 };
    renderRadial();
  });
  $("overview-zoom-in").addEventListener("click", function () { setOverviewScale(state.overviewScale + .1); });
  $("overview-zoom-out").addEventListener("click", function () { setOverviewScale(state.overviewScale - .1); });
  $("overview-fit-button").addEventListener("click", function () {
    state.overviewPan = { x: 0, y: 0 };
    state.overviewScale = 1;
    persistLayoutState();
    applyOverviewTransform();
  });
  $("radial-wrap").addEventListener("wheel", function (event) {
    event.preventDefault();
    var continuous = Math.abs(event.deltaY) < 50;
    if (event.ctrlKey || !continuous) {
      setSurfaceScale("focus", state.zoom + (event.deltaY < 0 ? .05 : -.05));
      return;
    }
    state.focusPan.x -= event.deltaX * .7;
    state.focusPan.y -= event.deltaY * .7;
    applyFocusPan();
  }, { passive: false });
  $("overview-wrap").addEventListener("wheel", function (event) {
    event.preventDefault();
    var continuous = Math.abs(event.deltaY) < 50;
    if (event.ctrlKey || !continuous) {
      setSurfaceScale("overview", state.overviewScale + (event.deltaY < 0 ? .08 : -.08));
      return;
    }
    state.overviewPan.x -= event.deltaX * .7;
    state.overviewPan.y -= event.deltaY * .7;
    applyOverviewTransform();
    persistLayoutState();
  }, { passive: false });
  $("overview-svg").addEventListener("pointerdown", function (event) {
    if (event.pointerType === "touch") {
      beginTouchPointer("overview", event);
      return;
    }
    if (event.button !== 0) return;
    var overviewSvg = $("overview-svg");
    state.overviewPanDrag = {
      pointerId: event.pointerId,
      startClient: { x: event.clientX, y: event.clientY },
      base: { x: state.overviewPan.x, y: state.overviewPan.y },
      moved: false
    };
    $("overview-svg").classList.add("is-panning");
    capturePointer(overviewSvg, event.pointerId);
  });
  $("overview-svg").addEventListener("pointermove", function (event) {
    if (event.pointerType === "touch") {
      moveTouchPointer("overview", event);
      return;
    }
    var drag = state.overviewPanDrag;
    if (!drag) return;
    var delta = svgClientDelta($("overview-svg"), drag.startClient, event, state.overviewScale);
    if (Math.abs(delta.x) + Math.abs(delta.y) > 3) drag.moved = true;
    state.overviewPan = {
      x: drag.base.x + delta.x,
      y: drag.base.y + delta.y
    };
    applyOverviewTransform();
  });
  $("overview-svg").addEventListener("pointerup", function (event) {
    if (event.pointerType === "touch") {
      endTouchPointer("overview", event);
      return;
    }
    var drag = state.overviewPanDrag;
    if (!drag) return;
    releasePointer($("overview-svg"), event.pointerId);
    state.overviewPanDrag = null;
    $("overview-svg").classList.remove("is-panning");
    if (drag.moved) {
      state.suppressOverviewClick = true;
      persistLayoutState();
    }
  });
  $("overview-svg").addEventListener("pointercancel", function () {
    state.overviewPanDrag = null;
    $("overview-svg").classList.remove("is-panning");
  });
  $("overview-svg").addEventListener("pointercancel", function (event) {
    if (event.pointerType === "touch") endTouchPointer("overview", event);
  });
  $("radial-svg").addEventListener("pointerdown", function (event) {
    if (event.pointerType === "touch") {
      beginTouchPointer("focus", event);
      return;
    }
    if (event.button !== 0) return;
    state.focusPanDrag = {
      pointerId: event.pointerId,
      startClient: { x: event.clientX, y: event.clientY },
      base: { x: state.focusPan.x, y: state.focusPan.y },
      moved: false
    };
    $("radial-svg").classList.add("is-panning");
    capturePointer($("radial-svg"), event.pointerId);
  });
  $("radial-svg").addEventListener("pointermove", function (event) {
    if (event.pointerType === "touch") {
      moveTouchPointer("focus", event);
      return;
    }
    var drag = state.focusPanDrag;
    if (!drag) return;
    var delta = svgClientDelta($("radial-svg"), drag.startClient, event, state.zoom);
    if (Math.abs(delta.x) + Math.abs(delta.y) > 3) drag.moved = true;
    state.focusPan = { x: drag.base.x + delta.x, y: drag.base.y + delta.y };
    applyFocusPan();
  });
  $("radial-svg").addEventListener("pointerup", function (event) {
    if (event.pointerType === "touch") {
      endTouchPointer("focus", event);
      return;
    }
    var drag = state.focusPanDrag;
    if (!drag) return;
    releasePointer($("radial-svg"), event.pointerId);
    state.focusPanDrag = null;
    $("radial-svg").classList.remove("is-panning");
    if (drag.moved) state.suppressRadialClick = true;
  });
  $("radial-svg").addEventListener("pointercancel", function () {
    state.focusPanDrag = null;
    $("radial-svg").classList.remove("is-panning");
  });
  $("radial-svg").addEventListener("pointercancel", function (event) {
    if (event.pointerType === "touch") endTouchPointer("focus", event);
  });
  window.addEventListener("popstate", function () {
    var nodeId = readNodeHash();
    if (nodeId) {
      if (!state.project) return;
      focusNode(nodeId, false).then(function () {
        if (state.focus && state.focus.focus && state.focus.focus.id === nodeId) {
          selectNode(nodeId);
        }
      });
    } else {
      state.selected = null;
      state.editing = false;
      renderDetail(null);
    }
  });

  window.addEventListener("beforeunload", stopEventStream);
  window.addEventListener("beforeunload", revokeReviewSession);

  loadReviewToken();
  loadProjects(null).then(function () {
    if (!state.project) return null;
    var nodeId = readNodeHash();
    if (!nodeId) return null;
    return focusNode(nodeId, false).then(function () {
      if (state.focus && state.focus.focus && state.focus.focus.id === nodeId) selectNode(nodeId);
      return null;
    });
  });
}());
