// 加工エディタ(ぼかし・モザイク)のポップアップ。静止画・動画とも、四角をドラッグで囲んで範囲を決める。
// 範囲は、画面の大きさに対する割合(0〜1)で持つので、プレビューの大きさと、元の解像度が違っても、同じ範囲になる。
// ぼかしのプレビューはCSSのぼかし、モザイクのプレビューはcanvasで描く(保存後の見た目に、近い値)。
(function () {
  const dialog = document.getElementById("mask-dialog");
  if (!dialog) return;
  const assetId = dialog.dataset.assetId;
  const isVideo = dialog.dataset.kind === "video";
  const $ = (id) => document.getElementById(id);
  const MAX_REGIONS = 8;
  const MIN_SIDE = 0.01;

  const media = isVideo ? $("mk-video") : $("mk-image");
  const stage = $("mk-stage");
  const layer = $("mk-layer");
  const runButton = $("mk-run");
  const notice = $("mk-notice");
  const strengthInput = $("mk-strength");

  let regions = [];
  let drawing = null;  // {x0, y0, el}

  const style = () => (dialog.querySelector('input[name="mk-style"]:checked') || {}).value || "blur";
  const strength = () => parseInt(strengthInput.value, 10) || 5;
  const naturalSize = () => (isVideo ? [media.videoWidth, media.videoHeight] : [media.naturalWidth, media.naturalHeight]);

  function showNotice(text, isError) {
    notice.hidden = !text;
    notice.textContent = text || "";
    notice.classList.toggle("ve-error", !!isError);
  }

  // サーバー(core/mask.py)と同じ式。長い辺に対する割合で、見た目の強さをそろえる
  const sigma = (s, longSide) => Math.max(2, (s * longSide) / 300);
  const block = (s, longSide) => Math.max(4, Math.round((s * longSide) / 160));

  function paint(rectEl, r) {
    const box = stage.getBoundingClientRect();
    const [nw, nh] = naturalSize();
    rectEl.classList.toggle("mk-mosaic", style() === "mosaic");
    rectEl.style.backdropFilter = "";
    const canvas = rectEl.querySelector("canvas");
    if (style() === "blur") {
      const displayLong = Math.max(box.width, box.height);
      rectEl.style.backdropFilter = `blur(${sigma(strength(), displayLong).toFixed(1)}px)`;
      canvas.hidden = true;
      return;
    }
    canvas.hidden = false;
    if (!nw || !nh) return;
    const cw = Math.max(1, Math.round(r.w * nw));
    const ch = Math.max(1, Math.round(r.h * nh));
    const b = block(strength(), Math.max(nw, nh));
    const sw = Math.max(1, Math.round(cw / b));
    const sh = Math.max(1, Math.round(ch / b));
    const small = document.createElement("canvas");
    small.width = sw;
    small.height = sh;
    try {
      small.getContext("2d").drawImage(media, r.x * nw, r.y * nh, r.w * nw, r.h * nh, 0, 0, sw, sh);
      canvas.width = cw;
      canvas.height = ch;
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(small, 0, 0, sw, sh, 0, 0, cw, ch);
    } catch (err) {
      /* 動画の準備前などは、描けない。次の更新で描く */
    }
  }

  function placeRect(el, r) {
    el.style.left = `${r.x * 100}%`;
    el.style.top = `${r.y * 100}%`;
    el.style.width = `${r.w * 100}%`;
    el.style.height = `${r.h * 100}%`;
  }

  function newRectEl(index) {
    const el = document.createElement("div");
    el.className = "mk-rect";
    el.append(document.createElement("canvas"));
    const label = document.createElement("span");
    label.className = "mk-rect-label";
    label.textContent = String(index);
    el.append(label);
    return el;
  }

  function render() {
    layer.querySelectorAll(".mk-rect:not(.mk-drawing)").forEach((n) => n.remove());
    regions.forEach((r, i) => {
      const el = newRectEl(i + 1);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "mk-rect-del";
      del.setAttribute("aria-label", `範囲${i + 1}を消す`);
      del.textContent = "×";
      del.addEventListener("pointerdown", (e) => e.stopPropagation());
      del.addEventListener("click", (e) => { e.stopPropagation(); regions.splice(i, 1); render(); });
      el.append(del);
      placeRect(el, r);
      layer.append(el);
      paint(el, r);
    });
    const chips = $("mk-chips");
    chips.replaceChildren(...regions.map((r, i) => {
      const li = document.createElement("li");
      li.textContent = `範囲${i + 1}（${Math.round(r.w * 100)}%×${Math.round(r.h * 100)}%）`;
      return li;
    }));
    $("mk-clear").hidden = regions.length === 0;
    $("mk-hint").textContent = regions.length ? `${regions.length}か所（最大${MAX_REGIONS}か所）。さらに囲むか、保存してください。` : "画面の上をドラッグして、範囲を囲んでください。";
    runButton.disabled = regions.length === 0;
  }

  function repaintAll() {
    layer.querySelectorAll(".mk-rect:not(.mk-drawing)").forEach((el, i) => { if (regions[i]) paint(el, regions[i]); });
  }

  // --- 範囲をドラッグで囲む ---
  const pos = (e) => {
    const box = layer.getBoundingClientRect();
    return [Math.min(Math.max((e.clientX - box.left) / box.width, 0), 1), Math.min(Math.max((e.clientY - box.top) / box.height, 0), 1)];
  };
  layer.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || regions.length >= MAX_REGIONS) {
      if (regions.length >= MAX_REGIONS) showNotice(`範囲は、${MAX_REGIONS}か所までです。不要な範囲を消してください。`, true);
      return;
    }
    const [x, y] = pos(e);
    const el = newRectEl("");
    el.classList.add("mk-drawing");
    layer.append(el);
    drawing = { x0: x, y0: y, el, rect: { x, y, w: 0, h: 0 } };
    layer.setPointerCapture(e.pointerId);
    showNotice("");
    e.preventDefault();
  });
  layer.addEventListener("pointermove", (e) => {
    if (!drawing) return;
    const [x, y] = pos(e);
    drawing.rect = { x: Math.min(x, drawing.x0), y: Math.min(y, drawing.y0), w: Math.abs(x - drawing.x0), h: Math.abs(y - drawing.y0) };
    placeRect(drawing.el, drawing.rect);
  });
  const finish = (e) => {
    if (!drawing) return;
    if (e && e.type === "pointerup") {  // 動きの途中の通知が無くても(素早いドラッグ)、離した位置で確定する
      const [x, y] = pos(e);
      drawing.rect = { x: Math.min(x, drawing.x0), y: Math.min(y, drawing.y0), w: Math.abs(x - drawing.x0), h: Math.abs(y - drawing.y0) };
    }
    const { rect, el } = drawing;
    el.remove();
    drawing = null;
    if (rect.w >= MIN_SIDE && rect.h >= MIN_SIDE) regions.push(rect);
    render();
  };
  layer.addEventListener("pointerup", finish);
  layer.addEventListener("pointercancel", finish);

  $("mk-clear").addEventListener("click", () => { regions = []; render(); });
  dialog.querySelectorAll('input[name="mk-style"]').forEach((r) => r.addEventListener("change", repaintAll));
  strengthInput.addEventListener("input", () => { $("mk-strength-value").textContent = String(strength()); repaintAll(); });
  window.addEventListener("resize", repaintAll);

  // --- 動画: 再生位置の移動(範囲の位置を決めるために、止めて見られるようにする) ---
  if (isVideo) {
    const seek = $("mk-seek");
    const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
    const timeText = () => { $("mk-time").textContent = `${fmt(media.currentTime || 0)} / ${fmt(media.duration || 0)}`; };
    media.addEventListener("loadedmetadata", () => { timeText(); repaintAll(); });
    media.addEventListener("timeupdate", () => {
      if (media.duration) seek.value = String(Math.round((media.currentTime / media.duration) * 1000));
      timeText();
      if (style() === "mosaic") repaintAll();
    });
    media.addEventListener("seeked", repaintAll);
    media.addEventListener("play", () => { $("mk-play").textContent = "⏸ 一時停止"; });
    media.addEventListener("pause", () => { $("mk-play").textContent = "▶ 再生"; });
    $("mk-play").addEventListener("click", () => { media.paused ? media.play().catch(() => {}) : media.pause(); });
    seek.addEventListener("input", () => { if (media.duration) media.currentTime = (seek.value / 1000) * media.duration; });
  } else {
    media.addEventListener("load", repaintAll);
  }

  function close() {
    if (isVideo) media.pause();
    dialog.close();
  }
  $("mk-close").addEventListener("click", close);
  $("mk-cancel").addEventListener("click", close);

  window.openMask = function () {
    regions = [];
    showNotice("");
    if (!media.getAttribute("src")) media.src = isVideo ? `${dialog.dataset.src}#t=0.1` : dialog.dataset.src;
    if (isVideo) { media.preload = "metadata"; media.load(); }
    render();
    if (!dialog.open) dialog.showModal();
  };

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-mask]");
    if (!opener) return;
    const menu = opener.closest("details");
    if (menu) menu.open = false;
    window.openMask();
  });

  runButton.addEventListener("click", async () => {
    runButton.disabled = true;
    showNotice("");
    try {
      const res = await fetch(`/api/assets/${assetId}/edits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "mask", regions, style: style(), strength: strength() }),
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
      runButton.disabled = regions.length === 0;
    }
  });
})();
