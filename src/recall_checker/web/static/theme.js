/*
 * Light, Dark or System: the one per-device preference (standards docs/design.md, Dark mode).
 *
 * Loaded by every page as a render-blocking <script src> in <head>, before the stylesheet (no
 * `defer`, `async` or module), so a stored choice applies before first paint and the page never
 * flashes the device's theme first. It is a file rather than inline because the CSP allows
 * scripts from this origin only (web/guards.py).
 *
 * "System" is no stored value and no `data-theme`, so the stylesheet's prefers-color-scheme
 * block decides. The choice lives in localStorage: it must apply with no request, and a phone
 * and a desk may reasonably differ. Storage can be refused (a private window, blocked site
 * data); then the device decides, and a choice made on the page holds until it is left.
 *
 * The buttons are in base.html, rendered hidden; this script marks the current choice and shows
 * them, so without it there is no control that does nothing.
 */
(function () {
  "use strict";
  var KEY = "theme";
  var unsaved = null;

  function stored() {
    if (unsaved) return unsaved;
    try {
      var value = window.localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : "system";
    } catch (_) {
      return "system";
    }
  }

  function apply(value) {
    var root = document.documentElement;
    if (value === "light" || value === "dark") root.setAttribute("data-theme", value);
    else root.removeAttribute("data-theme");
  }

  function choose(value) {
    unsaved = null;
    try {
      if (value === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, value);
    } catch (_) {
      unsaved = value;
    }
    apply(value);
  }

  function showPressed() {
    var current = stored();
    var buttons = document.querySelectorAll("[data-theme-choice] button[data-theme-value]");
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      b.setAttribute("aria-pressed", String(b.getAttribute("data-theme-value") === current));
    }
  }

  apply(stored());

  // Another tab changed it: follow.
  window.addEventListener("storage", function (event) {
    if (event.key !== KEY) return;
    apply(event.newValue);
    showPressed();
  });

  document.addEventListener("DOMContentLoaded", function () {
    var groups = document.querySelectorAll("[data-theme-choice]");
    for (var i = 0; i < groups.length; i++) {
      groups[i].addEventListener("click", function (event) {
        var button = event.target.closest("button[data-theme-value]");
        if (!button) return;
        choose(button.getAttribute("data-theme-value"));
        showPressed();
      });
      groups[i].hidden = false;
    }
    showPressed();
  });
})();
