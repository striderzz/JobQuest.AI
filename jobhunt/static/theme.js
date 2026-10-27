// Runs before the page paints, so there is no flash of the wrong theme.
// Each skin = [Bootstrap base mode, label]. Colours live in app.css under [data-skin=...];
// "custom" is a generated palette whose CSS variables are stored in localStorage and set here.
window.JQ_SKINS = { light: ["light", "Light"], dark: ["dark", "Dark"], minimal: ["light", "Minimal"],
  colorful: ["light", "Colorful"], ocean: ["light", "Ocean"], teal: ["light", "Teal"], rose: ["light", "Rose"],
  sand: ["light", "Sand"], lavender: ["light", "Lavender"], contrast: ["light", "High contrast"],
  forest: ["dark", "Forest"], sunset: ["dark", "Sunset"], slate: ["dark", "Slate"], midnight: ["dark", "Midnight"],
  graphite: ["dark", "Graphite"] };

(function () {
  var VARS = ["--bs-body-bg", "--bs-body-bg-rgb", "--bs-body-color", "--bs-secondary-color", "--jh-card", "--jh-card-2",
    "--jh-soft", "--jh-ink", "--jh-ink-hover", "--jh-on-ink", "--jh-nav-bg", "--bs-border-color", "--bs-focus-ring-color"];

  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

  // ---- colour maths (HSL -> sRGB, WCAG contrast)
  function hsl(h, s, l) {
    s /= 100; l /= 100;
    var a = s * Math.min(l, 1 - l);
    function f(n) { var k = (n + h / 30) % 12; return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))); }
    return [f(0), f(8), f(4)];
  }
  function lum(c) {
    var v = c.map(function (x) { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function css(c) { return "rgb(" + c.join(", ") + ")"; }

  // A random but always-readable palette: any hue, saturation and contrast level, light or dark.
  window.randomPalette = function () {
    var r = Math.random, dark = r() < 0.45;
    var h = Math.floor(r() * 360), sat = 35 + Math.floor(r() * 60), tint = 4 + Math.floor(r() * 26);
    var depth = r();                                         // how far the background sits from pure white / black
    var bg = dark ? hsl(h, tint, 5 + depth * 9) : hsl(h, tint, 99 - depth * 6);
    var card = dark ? hsl(h, tint, 9 + depth * 9) : hsl(h, tint, 100 - depth * 2);
    var card2 = dark ? hsl(h, tint, 12 + depth * 9) : hsl(h, tint, 97 - depth * 4);
    var soft = dark ? hsl(h, tint, 17 + depth * 8) : hsl(h, Math.min(60, tint + 20), 92 - depth * 4);
    var border = dark ? hsl(h, tint, 22 + depth * 6) : hsl(h, tint, 86 - depth * 4);
    var text = dark ? hsl(h, 12, 93) : hsl(h, 20, 9), muted = dark ? hsl(h, 10, 68) : hsl(h, 12, 36);
    var ah = (h + [0, 0, 30, 180, 150, 210][Math.floor(r() * 6)]) % 360;    // accent: same hue or a complement
    var l = dark ? 66 : 38, ink = hsl(ah, sat, l);
    while (contrast(ink, bg) < 4.5 && l > 8 && l < 92) { l += dark ? 3 : -3; ink = hsl(ah, sat, l); }  // keep it readable
    var onInk = contrast(ink, [255, 255, 255]) >= contrast(ink, [10, 10, 12]) ? [255, 255, 255] : [10, 10, 12];
    return { mode: dark ? "dark" : "light", hue: ah, vars: {
      "--bs-body-bg": css(bg), "--bs-body-bg-rgb": bg.join(", "), "--bs-body-color": css(text), "--bs-secondary-color": css(muted),
      "--jh-card": css(card), "--jh-card-2": css(card2), "--jh-soft": css(soft), "--jh-ink": css(ink),
      "--jh-ink-hover": css(hsl(ah, sat, dark ? Math.min(l + 8, 92) : Math.max(l - 8, 8))), "--jh-on-ink": css(onInk),
      "--jh-nav-bg": "rgba(" + bg.join(", ") + ", .88)", "--bs-border-color": css(border),
      "--bs-focus-ring-color": "rgba(" + ink.join(", ") + ", .25)" } };
  };

  window.applySkin = function (k, palette) {
    var root = document.documentElement;
    VARS.forEach(function (v) { root.style.removeProperty(v); });
    if (k === "custom") {
      var p = palette;
      if (!p) { try { p = JSON.parse(get("jh:custom")); } catch (e) { p = null; } }
      if (!p) { p = window.randomPalette(); }
      root.setAttribute("data-bs-theme", p.mode);
      root.setAttribute("data-skin", "custom");
      Object.keys(p.vars).forEach(function (v) { root.style.setProperty(v, p.vars[v]); });
      return p;
    }
    if (!window.JQ_SKINS[k]) k = "light";
    root.setAttribute("data-bs-theme", window.JQ_SKINS[k][0]);
    root.setAttribute("data-skin", k);
    return null;
  };

  var saved = get("jh:skin") || get("jh:theme");
  if (saved && saved.charAt(0) === '"') saved = saved.slice(1, -1);
  var q = (location.search.match(/[?&]skin=([\w-]+)/) || [])[1];      // ?skin=dark previews a theme without saving it
  if (q) saved = q;
  window.applySkin(saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
})();
