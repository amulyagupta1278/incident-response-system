(function () {
  "use strict";

  var script = document.currentScript || {};
  var endpoint = script.getAttribute("data-endpoint") || "/api/v1/browser/events";
  var projectId = script.getAttribute("data-project-id") || "";
  var publicKey = script.getAttribute("data-public-key") || script.getAttribute("data-ingest-key") || "";
  var service = script.getAttribute("data-service") || "website";
  var environment = script.getAttribute("data-environment") || "production";
  var releaseSha = script.getAttribute("data-release-sha") || "";
  var sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);

  function redact(text) {
    return String(text || "")
      .replace(/([?&](?:token|key|secret|password|auth|authorization)=)[^&#\s]+/gi, "$1[REDACTED]")
      .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[REDACTED_EMAIL]")
      .slice(0, 2000);
  }

  function route() {
    return location.pathname || "/";
  }

  function send(event) {
    if (!publicKey || !projectId) return;
    var payload = Object.assign(
      {
        project_id: projectId,
        public_key: publicKey,
        service: service,
        environment: environment,
        page_url: redact(location.href),
        route: route(),
        release_sha: releaseSha,
        session_id: sessionId,
        user_agent: navigator.userAgent,
        timestamp: new Date().toISOString()
      },
      event
    );
    var body = JSON.stringify(payload);
    if (navigator.sendBeacon) {
      var blob = new Blob([body], { type: "application/json" });
      if (navigator.sendBeacon(endpoint, blob)) return;
    }
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + publicKey },
      body: body,
      keepalive: true
    }).catch(function () {});
  }

  window.addEventListener("error", function (event) {
    send({
      event_type: "browser_error",
      message: redact(event.message),
      stack: redact(event.error && event.error.stack)
    });
  });

  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason || {};
    send({
      event_type: "browser_error",
      message: redact(reason.message || reason),
      stack: redact(reason.stack)
    });
  });

  var originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function () {
      var started = performance.now();
      var input = arguments[0];
      var apiUrl = typeof input === "string" ? input : input && input.url;
      return originalFetch.apply(this, arguments).then(
        function (response) {
          if (response.status >= 400) {
            send({
              event_type: "api_failure",
              message: "fetch failed with status " + response.status,
              api_url: redact(apiUrl),
              status_code: response.status,
              duration_ms: Math.round(performance.now() - started)
            });
          }
          return response;
        },
        function (error) {
          send({
            event_type: "api_failure",
            message: redact(error && error.message),
            stack: redact(error && error.stack),
            api_url: redact(apiUrl),
            duration_ms: Math.round(performance.now() - started)
          });
          throw error;
        }
      );
    };
  }

  window.addEventListener("load", function () {
    setTimeout(function () {
      var nav = performance.getEntriesByType && performance.getEntriesByType("navigation")[0];
      if (!nav) return;
      send({
        event_type: "frontend_performance",
        message: "page load timing",
        duration_ms: Math.round(nav.loadEventEnd || nav.duration || 0)
      });
    }, 0);
  });

  window.ImmuneAgent = {
    capture: function (message, data) {
      send(Object.assign({ event_type: "browser_error", message: redact(message) }, data || {}));
    }
  };
})();
