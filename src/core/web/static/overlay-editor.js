// 加工エディタ(テロップ・スタンプ)。静止画・動画とも。
// 重ねるものを「レイヤー」として持つ。各レイヤーの見た目は、サーバーの描画(/api/overlay/layer-preview)で作った透明PNGを、
// 作品の上に置いて見せる(保存後の仕上がりと同じ描画なので、見たとおりになる)。位置・大きさは、画面に対する割合で持つ。
(function () {
  const dialog = document.getElementById("overlay-dialog");
  if (!dialog) return;
  const assetId = dialog.dataset.assetId;
  const isVideo = dialog.dataset.kind === "video";
  const $ = (id) => document.getElementById(id);
  const media = isVideo ? $("ov-video") : $("ov-image");
  const stage = $("ov-stage");
  const layersEl = $("ov-layers");
  const runButton = $("ov-run");
  const notice = $("ov-notice");

  let options = null;
  let layers = [];
  let selected = null;
  let seq = 0;
  let stampTab = "preset";

  const TEXT_STYLES = [
    { label: "白＋黒縁", v: { fill: "#ffffff", stroke_color: "#000000", stroke_width: 0.1, bold: true, shadow: false, bg: false } },
    { label: "黄＋赤縁", v: { fill: "#ffe600", stroke_color: "#e60012", stroke_width: 0.12, bold: true, shadow: true, shadow_color: "#000000", bg: false } },
    { label: "ピンク＋白縁", v: { fill: "#ff4fa3", stroke_color: "#ffffff", stroke_width: 0.14, bold: true, shadow: true, shadow_color: "#7a1040", bg: false } },
    { label: "影だけ", v: { fill: "#ffffff", stroke_width: 0, bold: true, shadow: true, shadow_color: "#000000", shadow_blur: 0.06, bg: false } },
    { label: "黒帯つき", v: { fill: "#ffffff", stroke_width: 0, bold: false, shadow: false, bg: true, bg_color: "#000000", bg_opacity: 0.7 } },
  ];
  const ANIM_DEFAULT = { type: "none", cycle: 6, loop: true, start: 0, end: null, fade_in: 0, fade_out: 0 };

  function showNotice(text, isError) {
    notice.hidden = !text;
    notice.textContent = text || "";
    notice.classList.toggle("ve-error", !!isError);
  }
  const naturalSize = () => (isVideo ? [media.videoWidth, media.videoHeight] : [media.naturalWidth, media.naturalHeight]);
  // プレビューの描画に使う画面の大きさ(長い辺を、1000pxまでに縮めた大きさ)。割合で持つので、保存後と同じ見た目になる
  function previewDims() {
    const [w, h] = naturalSize();
    if (!w || !h) return [800, 600];
    const k = Math.min(1, 1000 / Math.max(w, h));
    return [Math.round(w * k), Math.round(h * k)];
  }
  const clientKeys = (l) => Object.fromEntries(Object.entries(l).filter(([k]) => !k.startsWith("_")));

  // ---------------------------------------------------------------- レイヤーの描画
  const timers = new Map();
  function scheduleRender(layer, delay) {
    clearTimeout(timers.get(layer._id));
    timers.set(layer._id, setTimeout(() => renderLayer(layer), delay === undefined ? 180 : delay));
  }
  async function renderLayer(layer) {
    if (!layers.includes(layer)) return;
    if (layer.type === "text" && !String(layer.text || "").trim()) { layer._img.style.visibility = "hidden"; return; }
    const [pw, ph] = previewDims();
    try {
      const res = await fetch("/api/overlay/layer-preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ layer: clientKeys(layer), width: pw, height: ph }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.error || "プレビューを作れませんでした", true);
        return;
      }
      showNotice("");
      const url = URL.createObjectURL(await res.blob());
      const old = layer._img.src;
      layer._img.onload = () => {
        layer._pw = pw;
        layer._iw = layer._img.naturalWidth;
        place(layer);
        layer._img.style.visibility = "visible";
        if (old && old.startsWith("blob:")) URL.revokeObjectURL(old);
      };
      layer._img.src = url;
    } catch (err) {
      /* 通信の失敗は、次の変更で、やり直す */
    }
  }
  function place(layer) {
    const el = layer._img;
    el.style.left = `${layer.x * 100}%`;
    el.style.top = `${layer.y * 100}%`;
    if (layer._iw && layer._pw) el.style.width = `${(layer._iw / layer._pw) * 100}%`;
  }
  function renderAll() { layers.forEach((l) => scheduleRender(l, 0)); }

  // ---------------------------------------------------------------- レイヤーの追加・選択・並べ替え
  function addLayer(layer) {
    layer._id = ++seq;
    layer.anim = { ...ANIM_DEFAULT, ...(layer.anim || {}) };
    const img = document.createElement("img");
    img.className = "ov-layer";
    img.draggable = false;
    img.alt = "";
    img.style.visibility = "hidden";
    img.addEventListener("pointerdown", (e) => startDrag(e, layer));
    layer._img = img;
    layers.push(layer);
    layersEl.append(img);
    select(layer);
    scheduleRender(layer, 0);
    refreshList();
    // スマホでは、設定が画面の下にあるため、追加したら、そこまで動かす
    if (window.innerWidth <= 760) $("ov-list").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function newText() {
    const l = {
      type: "text", text: "テキスト", font: options.default_font, size: 0.08, x: 0.5, y: 0.82, rotation: 0, opacity: 1,
      bold: true, italic: false, fill: "#ffffff", stroke_color: "#000000", stroke_width: 0.1,
      shadow: false, shadow_color: "#000000", shadow_dx: 0.06, shadow_dy: 0.06, shadow_blur: 0.04,
      bg: false, bg_color: "#000000", bg_opacity: 0.6, align: "center",
    };
    addLayer(l);
  }
  function newStamp() {
    const first = options.stamps.find((s) => s.id === "preset:bar-black") || options.stamps[0];
    addLayer({ type: "stamp", stamp: first.id, size: 0.35, x: 0.5, y: 0.5, rotation: 0, opacity: 1 });
  }
  function select(layer) {
    selected = layer;
    layers.forEach((l) => l._img.classList.toggle("ov-selected", l === layer));
    refreshList();
    buildProps();
  }
  function removeLayer(layer) {
    const i = layers.indexOf(layer);
    if (i < 0) return;
    layers.splice(i, 1);
    if (layer._img.src.startsWith("blob:")) URL.revokeObjectURL(layer._img.src);
    layer._img.remove();
    select(layers[Math.min(i, layers.length - 1)] || null);
  }
  function move(layer, delta) {
    const i = layers.indexOf(layer);
    const j = i + delta;
    if (j < 0 || j >= layers.length) return;
    [layers[i], layers[j]] = [layers[j], layers[i]];
    layers.forEach((l) => layersEl.append(l._img));
    refreshList();
  }
  const summary = (l) => (l.type === "text" ? `テロップ「${String(l.text).replace(/\n/g, " ").slice(0, 10)}」` : `スタンプ: ${(options.stamps.find((s) => s.id === l.stamp) || {}).label || ""}`);
  function refreshList() {
    $("ov-list").replaceChildren(...layers.map((l, i) => {
      const li = document.createElement("li");
      li.className = l === selected ? "ov-item ov-item-on" : "ov-item";
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ov-item-main";
      b.textContent = `${i + 1}. ${summary(l)}`;
      b.addEventListener("click", () => select(l));
      const tools = document.createElement("span");
      tools.className = "ov-item-tools";
      for (const [label, fn, tip] of [["↑", () => move(l, -1), "後ろへ"], ["↓", () => move(l, 1), "前へ"], ["複製", () => duplicate(l), "同じものを増やす"], ["×", () => removeLayer(l), "消す"]]) {
        const t = document.createElement("button");
        t.type = "button";
        t.textContent = label;
        t.title = tip;
        t.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
        tools.append(t);
      }
      li.append(b, tools);
      return li;
    }));
    runButton.disabled = layers.length === 0;
    $("ov-count").textContent = layers.length ? `${layers.length}個（最大${options.max_layers}個）` : "";
    $("ov-add-text").disabled = $("ov-add-stamp").disabled = layers.length >= options.max_layers;
  }
  function duplicate(layer) {
    if (layers.length >= options.max_layers) return;
    const copy = JSON.parse(JSON.stringify(clientKeys(layer)));
    copy.x = Math.min(1, layer.x + 0.04);
    copy.y = Math.min(1, layer.y + 0.04);
    addLayer(copy);
  }

  // ---------------------------------------------------------------- ドラッグで動かす
  function startDrag(e, layer) {
    if (e.button !== 0) return;
    select(layer);
    const box = stage.getBoundingClientRect();
    const move = (ev) => {
      layer.x = Math.min(Math.max((ev.clientX - box.left) / box.width, 0), 1);
      layer.y = Math.min(Math.max((ev.clientY - box.top) / box.height, 0), 1);
      place(layer);
    };
    const up = (ev) => {
      move(ev);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    e.preventDefault();
  }

  // ---------------------------------------------------------------- 右の設定パネル
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const range = (f, label, min, max, step, value, fmt) =>
    `<label class="ov-field">${label} <b data-out="${f}">${fmt(value)}</b><input type="range" data-f="${f}" min="${min}" max="${max}" step="${step}" value="${value}"></label>`;
  const color = (f, label, value) => `<label class="ov-field ov-color">${label}<input type="color" data-f="${f}" value="${value}"></label>`;
  const check = (f, label, value) => `<label class="ov-inline"><input type="checkbox" data-f="${f}" ${value ? "checked" : ""}> ${label}</label>`;
  const pct = (v) => `${Math.round(v * 100)}%`;

  function buildProps() {
    const box = $("ov-props");
    const l = selected;
    if (!l) { box.innerHTML = '<p class="hint">「＋ テロップ」か「＋ スタンプ」で、追加してください。</p>'; return; }
    let html = "";
    if (l.type === "text") {
      html += `<label class="ov-field">文字（改行できます）<textarea data-f="text" rows="2" maxlength="200">${esc(l.text)}</textarea></label>`;
      html += `<label class="ov-field">フォント<select data-f="font">${options.fonts.map((f) => `<option value="${f.id}" ${f.id === l.font ? "selected" : ""}>${esc(f.label)}</option>`).join("")}</select></label>`;
      html += `<div class="ov-styles">${TEXT_STYLES.map((s, i) => `<button type="button" class="btn-ghost-inline" data-style="${i}">${s.label}</button>`).join("")}</div>`;
      html += `<div class="ov-styles">${options.styles.map((s) => `<span class="ov-saved"><button type="button" class="btn-ghost-inline" data-saved-style="${s.id}" title="保存したスタイルを当てはめる">★${esc(s.name)}</button><button type="button" class="ov-saved-del" data-del-style="${s.id}" title="このスタイルを削除">×</button></span>`).join("")}</div>`;
      html += `<div class="ov-row ov-save-row"><input type="text" id="ov-style-name" maxlength="30" placeholder="スタイル名（例: 宣伝・黄色）"><button type="button" class="btn-ghost-inline" id="ov-style-save">いまの見た目を、スタイルとして保存</button></div>`;
      html += range("size", "文字の大きさ", 0.02, 0.4, 0.005, l.size, (v) => `${(v * 100).toFixed(1)}%`);
      html += `<div class="ov-row">${check("bold", "太字", l.bold)}${check("italic", "斜体", l.italic)}
        <label class="ov-inline">配置 <select data-f="align">${[["left", "左"], ["center", "中央"], ["right", "右"]].map(([v, t]) => `<option value="${v}" ${l.align === v ? "selected" : ""}>${t}</option>`).join("")}</select></label></div>`;
      html += `<div class="ov-row">${color("fill", "文字色", l.fill)}${color("stroke_color", "縁の色", l.stroke_color)}</div>`;
      html += range("stroke_width", "縁の太さ", 0, 0.3, 0.005, l.stroke_width, pct);
      html += `<div class="ov-row">${check("shadow", "影をつける", l.shadow)}${color("shadow_color", "影の色", l.shadow_color)}</div>`;
      if (l.shadow) html += range("shadow_dx", "影の横位置", -0.2, 0.2, 0.005, l.shadow_dx, pct) + range("shadow_dy", "影の縦位置", -0.2, 0.2, 0.005, l.shadow_dy, pct) + range("shadow_blur", "影のぼかし", 0, 0.3, 0.005, l.shadow_blur, pct);
      html += `<div class="ov-row">${check("bg", "背景の帯", l.bg)}${color("bg_color", "帯の色", l.bg_color)}</div>`;
      if (l.bg) html += range("bg_opacity", "帯の濃さ", 0, 1, 0.05, l.bg_opacity, pct);
    } else {
      const groups = { preset: "図形・バッジ", emoji: "絵文字", upload: "自分の画像" };
      html += `<div class="ov-tabs">${Object.entries(groups).map(([k, t]) => `<button type="button" class="${k === stampTab ? "on" : ""}" data-tab="${k}">${t}</button>`).join("")}</div>`;
      html += `<div class="ov-palette">${options.stamps.filter((s) => s.group === stampTab).map((s) =>
        `<button type="button" class="ov-stamp ${s.id === l.stamp ? "on" : ""}" data-stamp="${s.id}" title="${esc(s.label)}"><img src="/api/overlay/stamps/${encodeURIComponent(s.id)}/image" alt="${esc(s.label)}"></button>`).join("")
        || '<span class="hint">まだありません。下のボタンから、追加できます。</span>'}</div>`;
      html += `<div class="ov-row"><label class="btn-ghost-inline ov-upload">画像を追加…<input type="file" id="ov-upload" accept="image/*" hidden></label>`;
      if (stampTab === "upload" && l.stamp.startsWith("upload:")) html += `<button type="button" class="btn-ghost-inline" id="ov-delstamp">この画像を削除</button>`;
      html += `</div><p class="hint">ロゴや図柄の画像（PNG・JPEG・WebP）を追加すると、いつでも使えます。背景が透明なPNGがきれいです。</p>`;
      html += range("size", "大きさ（画面の幅に対して）", 0.03, 1, 0.01, l.size, pct);
    }
    html += range("rotation", "角度", -180, 180, 1, l.rotation, (v) => `${Math.round(v)}°`);
    html += range("opacity", "不透明度", 0.05, 1, 0.05, l.opacity, pct);
    html += `<div class="ov-pos"><span class="hint">位置:</span>
      ${[["上", 0.5, 0.12], ["中央", 0.5, 0.5], ["下", 0.5, 0.88]].map(([t, x, y]) => `<button type="button" class="btn-ghost-inline" data-pos="${x},${y}">${t}</button>`).join("")}</div>`;
    if (isVideo) {
      const a = l.anim;
      html += `<fieldset class="ov-anim"><legend>動画での動き・時間</legend>
        <label class="ov-field">動き<select data-f="anim.type">${Object.entries(options.anims).map(([v, t]) => `<option value="${v}" ${a.type === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;
      if (a.type !== "none") html += `<label class="ov-field">端から端まで流れる秒数<input type="number" data-f="anim.cycle" min="0.5" max="300" step="0.5" value="${a.cycle}"></label>${check("anim.loop", "繰り返す", a.loop)}`;
      html += `<div class="ov-row"><label class="ov-field">フェードイン（秒）<input type="number" data-f="anim.fade_in" min="0" max="30" step="0.1" value="${a.fade_in || 0}"></label>
        <label class="ov-field">フェードアウト（秒）<input type="number" data-f="anim.fade_out" min="0" max="30" step="0.1" value="${a.fade_out || 0}"></label></div>`;
      html += `<div class="ov-row"><label class="ov-field">表示の開始（秒）<input type="number" data-f="anim.start" min="0" step="0.1" value="${a.start}"></label>
        <label class="ov-field">表示の終了（秒・空欄で最後まで）<input type="number" data-f="anim.end" min="0" step="0.1" value="${a.end === null ? "" : a.end}"></label></div>
        <div class="ov-row"><button type="button" class="btn-ghost-inline" id="ov-set-start">現在位置を開始に</button><button type="button" class="btn-ghost-inline" id="ov-set-end">現在位置を終了に</button></div></fieldset>`;
    }
    box.innerHTML = html;
  }

  function setField(layer, path, value) {
    if (path.startsWith("anim.")) layer.anim[path.slice(5)] = value; else layer[path] = value;
  }
  $("ov-props").addEventListener("input", (e) => {
    const t = e.target;
    const path = t.dataset.f;
    if (!path || !selected) return;
    let value;
    if (t.type === "checkbox") value = t.checked;
    else if (t.type === "range" || t.type === "number") value = t.value === "" ? (path === "anim.end" ? null : 0) : parseFloat(t.value);
    else value = t.value;
    setField(selected, path, value);
    const out = $("ov-props").querySelector(`[data-out="${path}"]`);
    if (out) out.textContent = path === "rotation" ? `${Math.round(value)}°` : path === "size" && selected.type === "text" ? `${(value * 100).toFixed(1)}%` : pct(value);
    if (path !== "anim.type" && path !== "shadow" && path !== "bg" && !path.startsWith("anim.")) scheduleRender(selected);
    if (path === "text") refreshList();
  });
  // 構成が変わる項目(影・背景・動きの種類)は、パネルを作り直す
  $("ov-props").addEventListener("change", (e) => {
    const path = e.target.dataset.f;
    if (["shadow", "bg", "anim.type"].includes(path)) { buildProps(); if (path !== "anim.type") scheduleRender(selected, 0); }
  });
  $("ov-props").addEventListener("click", async (e) => {
    if (!selected) return;
    const style = e.target.closest("[data-style]");
    if (style) { Object.assign(selected, TEXT_STYLES[+style.dataset.style].v); buildProps(); scheduleRender(selected, 0); return; }
    const saved = e.target.closest("[data-saved-style]");
    if (saved) { Object.assign(selected, options.styles.find((s) => s.id === saved.dataset.savedStyle).style); buildProps(); scheduleRender(selected, 0); return; }
    const delStyle = e.target.closest("[data-del-style]");
    if (delStyle) { await fetch(`/api/overlay/styles/${delStyle.dataset.delStyle}`, { method: "DELETE" }); await loadOptions(); buildProps(); return; }
    if (e.target.id === "ov-style-save") {
      const name = $("ov-style-name").value.trim();
      try {
        const res = await fetch("/api/overlay/styles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, layer: clientKeys(selected) }) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "保存できませんでした");
        await loadOptions();
        buildProps();
        window.showToast(`スタイル「${data.name}」を保存しました`, "success");
      } catch (err) { window.showToast(err.message, "error"); }
      return;
    }
    const tab = e.target.closest("[data-tab]");
    if (tab) { stampTab = tab.dataset.tab; buildProps(); return; }
    const stamp = e.target.closest("[data-stamp]");
    if (stamp) { selected.stamp = stamp.dataset.stamp; buildProps(); refreshList(); scheduleRender(selected, 0); return; }
    const pos = e.target.closest("[data-pos]");
    if (pos) { [selected.x, selected.y] = pos.dataset.pos.split(",").map(Number); place(selected); return; }
    if (e.target.id === "ov-set-start") { selected.anim.start = +(media.currentTime || 0).toFixed(1); buildProps(); return; }
    if (e.target.id === "ov-set-end") { selected.anim.end = +(media.currentTime || 0).toFixed(1); buildProps(); return; }
    if (e.target.id === "ov-delstamp") {
      const ok = await window.confirmDialog("この画像を、スタンプの一覧から削除します（すでに作った加工版には影響しません）。よろしいですか？");
      if (!ok) return;
      await fetch(`/api/overlay/stamps/${encodeURIComponent(selected.stamp)}`, { method: "DELETE" });
      await loadOptions();
      selected.stamp = (options.stamps.find((s) => s.group === "preset") || options.stamps[0]).id;
      stampTab = "preset";
      buildProps(); refreshList(); scheduleRender(selected, 0);
    }
  });
  $("ov-props").addEventListener("change", async (e) => {
    if (e.target.id !== "ov-upload" || !e.target.files.length) return;
    const body = new FormData();
    body.append("file", e.target.files[0]);
    try {
      const res = await fetch("/api/overlay/stamps", { method: "POST", body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "追加できませんでした");
      await loadOptions();
      selected.stamp = data.id;
      stampTab = "upload";
      buildProps(); refreshList(); scheduleRender(selected, 0);
      window.showToast("スタンプに追加しました", "success");
    } catch (err) {
      window.showToast(err.message, "error");
    }
  });

  // ---------------------------------------------------------------- 動画のプレーヤー
  if (isVideo) {
    const seek = $("ov-seek");
    const text = () => { $("ov-time").textContent = `${(media.currentTime || 0).toFixed(1)} / ${(media.duration || 0).toFixed(1)} 秒`; };
    media.addEventListener("loadedmetadata", () => { text(); renderAll(); });
    media.addEventListener("timeupdate", () => { if (media.duration) seek.value = String(Math.round((media.currentTime / media.duration) * 1000)); text(); });
    media.addEventListener("play", () => { $("ov-play").textContent = "⏸ 一時停止"; });
    media.addEventListener("pause", () => { $("ov-play").textContent = "▶ 再生"; });
    $("ov-play").addEventListener("click", () => { media.paused ? media.play().catch(() => {}) : media.pause(); });
    seek.addEventListener("input", () => { if (media.duration) media.currentTime = (seek.value / 1000) * media.duration; });
  } else {
    media.addEventListener("load", renderAll);
  }

  // ---------------------------------------------------------------- 開く・閉じる・保存
  async function loadOptions() {
    options = await (await fetch("/api/overlay/options")).json();
  }
  function close() {
    if (isVideo) media.pause();
    dialog.close();
  }
  $("ov-close").addEventListener("click", close);
  $("ov-cancel").addEventListener("click", close);
  // テンプレート(レイヤーの組み合わせ全体)。保存したものは、あとから読み込める。一覧の「テロップ・スタンプを一括適用」でも使う
  function refreshTemplates() {
    const sel = $("ov-tpl");
    sel.replaceChildren(new Option("テンプレートを読み込む…", ""), ...options.templates.map((t) => new Option(`${t.name}（${t.layers.length}個）`, t.id)));
    $("ov-tpl-del").disabled = true;
  }
  $("ov-tpl").addEventListener("change", () => {
    const t = options.templates.find((x) => x.id === $("ov-tpl").value);
    $("ov-tpl-del").disabled = !t;
    if (!t) return;
    layers.splice(0).forEach((l) => l._img.remove());
    selected = null;
    t.layers.forEach((l) => addLayer(JSON.parse(JSON.stringify(l))));
    select(layers[0] || null);
    $("ov-tpl-name").value = t.name;
  });
  $("ov-tpl-del").addEventListener("click", async () => {
    const t = options.templates.find((x) => x.id === $("ov-tpl").value);
    if (!t || !(await window.confirmDialog(`テンプレート「${t.name}」を削除します。よろしいですか？`))) return;
    await fetch(`/api/overlay/templates/${t.id}`, { method: "DELETE" });
    await loadOptions();
    refreshTemplates();
  });
  $("ov-tpl-save").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/overlay/templates", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("ov-tpl-name").value, layers: layers.map(clientKeys) }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "保存できませんでした");
      await loadOptions();
      refreshTemplates();
      window.showToast(`テンプレート「${data.name}」を保存しました。一覧で複数の作品を選んで、一括適用できます`, "success");
    } catch (err) { window.showToast(err.message, "error"); }
  });
  $("ov-add-text").addEventListener("click", newText);
  $("ov-add-stamp").addEventListener("click", newStamp);

  window.openOverlay = async function () {
    showNotice("");
    await loadOptions();
    refreshTemplates();
    layers.splice(0).forEach((l) => l._img.remove());
    selected = null;
    if (!media.getAttribute("src")) media.src = isVideo ? `${dialog.dataset.src}#t=0.1` : dialog.dataset.src;
    if (isVideo) { media.preload = "metadata"; media.load(); }
    refreshList();
    buildProps();
    if (!dialog.open) dialog.showModal();
  };
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-overlay]");
    if (!opener) return;
    const menu = opener.closest("details");
    if (menu) menu.open = false;
    window.openOverlay();
  });

  runButton.addEventListener("click", async () => {
    runButton.disabled = true;
    showNotice("");
    try {
      const res = await fetch(`/api/assets/${assetId}/edits`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "overlay", layers: layers.map(clientKeys) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `登録できませんでした (${res.status})`);
      close();
      window.showToast("加工版の作成を登録しました。終わると、ファイル一覧と通知に出ます", "success");
      if (window.refreshLibrary) window.refreshLibrary(true);
    } catch (err) {
      showNotice(err.message, true);
      window.showToast(err.message, "error");
    } finally {
      runButton.disabled = layers.length === 0;
    }
  });
})();
