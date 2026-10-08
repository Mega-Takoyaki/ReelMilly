// 作品詳細画面: 動画の編集(切り出し)と、編集した動画(サブ動画)の一覧。
// - 開始・終了は、秒の数値か「現在位置を開始/終了に」で指定する(時間軸のクリックでその位置へ移動できる)
// - 切り出しは非同期ジョブ。登録後は、処理中のものがあるあいだだけ、一覧を定期的に取得して表示を更新する
(function () {
  const root = document.getElementById("video-edit");
  if (!root) return;
  const assetId = root.dataset.assetId;

  const video = document.getElementById("ve-video");
  const startInput = document.getElementById("ve-start");
  const endInput = document.getElementById("ve-end");
  const lengthEl = document.getElementById("ve-length");
  const timeline = document.getElementById("ve-timeline");
  const rangeEl = document.getElementById("ve-range");
  const playhead = document.getElementById("ve-playhead");
  const runButton = document.getElementById("ve-run");
  const previewButton = document.getElementById("ve-preview");
  const listEl = document.getElementById("ve-list");
  const countEl = document.getElementById("ve-count");
  const notice = document.getElementById("ve-notice");

  const STATUS = { queued: "待機中", running: "処理中", done: "", failed: "失敗" };
  let duration = 0;
  let previewEnd = null;
  let timer = null;
  let known = {}; // 編集ID → 前回の状態(完了の検知用)
  let atLimit = false;

  const fmt = (s) => {
    const tenths = Math.round(s * 10);
    return `${Math.floor(tenths / 600)}:${String(Math.floor((tenths % 600) / 10)).padStart(2, "0")}.${tenths % 10}`;
  };
  const size = (bytes) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`);
  const num = (input) => parseFloat(input.value);

  function showNotice(text, isError) {
    notice.hidden = !text;
    notice.textContent = text || "";
    notice.classList.toggle("ve-error", !!isError);
  }

  function refresh() {
    const s = num(startInput);
    const e = num(endInput);
    const ok = Number.isFinite(s) && Number.isFinite(e) && e > s && s >= 0;
    lengthEl.textContent = ok ? `長さ ${(e - s).toFixed(1)}秒` + (duration ? `（元は ${duration.toFixed(1)}秒）` : "") : "終了は、開始より後にしてください";
    if (duration && ok) {
      rangeEl.style.left = `${(s / duration) * 100}%`;
      rangeEl.style.width = `${(Math.min(e, duration) - s) / duration * 100}%`;
    }
    runButton.disabled = !ok || atLimit;
  }

  function setValue(input, value) {
    input.value = (Math.round(value * 10) / 10).toString();
    refresh();
  }

  video.addEventListener("loadedmetadata", () => {
    duration = video.duration || 0;
    if (duration) {
      endInput.max = duration.toFixed(1);
      startInput.max = duration.toFixed(1);
      setValue(endInput, duration);
    }
    refresh();
  });
  video.addEventListener("timeupdate", () => {
    if (duration) playhead.style.left = `${(video.currentTime / duration) * 100}%`;
    if (previewEnd !== null && video.currentTime >= previewEnd) {
      video.pause();
      previewEnd = null;
    }
  });
  video.addEventListener("pause", () => { if (previewEnd !== null && video.currentTime < previewEnd) previewEnd = null; });

  timeline.addEventListener("click", (e) => {
    if (!duration) return;
    const rect = timeline.getBoundingClientRect();
    video.currentTime = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1) * duration;
  });
  document.getElementById("ve-set-start").addEventListener("click", () => setValue(startInput, video.currentTime));
  document.getElementById("ve-set-end").addEventListener("click", () => setValue(endInput, video.currentTime));
  startInput.addEventListener("input", refresh);
  endInput.addEventListener("input", refresh);

  previewButton.addEventListener("click", () => {
    const s = num(startInput);
    const e = num(endInput);
    if (!(e > s)) return;
    video.currentTime = s;
    previewEnd = e;
    video.play().catch(() => {});
  });

  runButton.addEventListener("click", async () => {
    const mode = (root.querySelector('input[name="ve-mode"]:checked') || {}).value || "accurate";
    runButton.disabled = true;
    showNotice("");
    try {
      const res = await fetch(`/api/assets/${assetId}/edits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "trim", start: num(startInput), end: num(endInput), mode }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `登録できませんでした (${res.status})`);
      window.showToast("切り出しを登録しました。終わると、下の一覧と通知に出ます", "success");
      await load();
    } catch (err) {
      showNotice(err.message, true);
      window.showToast(err.message, "error");
    } finally {
      refresh();
    }
  });

  function item(e) {
    const li = document.createElement("li");
    li.className = `ve-item ve-${e.status}`;
    li.dataset.id = e.id;
    const main = document.createElement("div");
    main.className = "ve-main";
    const sum = document.createElement("span");
    sum.className = "ve-summary";
    sum.textContent = e.summary;
    main.append(sum);
    if (STATUS[e.status]) {
      const badge = document.createElement("span");
      badge.className = `badge ve-badge ve-badge-${e.status}`;
      badge.textContent = STATUS[e.status];
      main.append(badge);
    }
    const meta = document.createElement("span");
    meta.className = "hint";
    if (e.status === "done") meta.textContent = [e.duration ? `${e.duration.toFixed(1)}秒` : null, e.size_bytes ? size(e.size_bytes) : null].filter(Boolean).join(" ／ ");
    if (e.status === "failed") meta.textContent = e.error || "理由不明";
    main.append(meta);
    li.append(main);

    const actions = document.createElement("div");
    actions.className = "ve-links";
    if (e.status === "done" && e.available) {
      const open = document.createElement("a");
      open.href = e.url;
      open.target = "_blank";
      open.rel = "noopener";
      open.textContent = "動画を開く";
      const dl = document.createElement("a");
      dl.href = e.download_url;
      dl.textContent = "ダウンロード";
      dl.setAttribute("download", "");
      actions.append(open, dl);
    } else if (e.status === "done") {
      const miss = document.createElement("span");
      miss.className = "hint";
      miss.textContent = "ファイルが見つかりません";
      actions.append(miss);
    }
    if (e.status !== "running") {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn-ghost-inline ve-delete";
      del.textContent = "削除";
      del.addEventListener("click", () => remove(e));
      actions.append(del);
    }
    li.append(actions);
    return li;
  }

  async function remove(e) {
    const ok = await window.confirmDialog(`「${e.summary}」の編集動画を削除します（元の動画は変わりません）。よろしいですか？`);
    if (!ok) return;
    const res = await fetch(`/api/assets/${assetId}/edits/${e.id}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) window.showToast(data.error || "削除できませんでした", "error");
    await load();
  }

  async function load() {
    clearTimeout(timer);
    let data;
    try {
      const res = await fetch(`/api/assets/${assetId}/edits`);
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
    } catch (err) {
      timer = setTimeout(load, 8000);
      return;
    }
    countEl.textContent = `${data.used}/${data.limit}件`;
    atLimit = data.used >= data.limit;
    if (!data.ffmpeg) showNotice("ffmpegが見つかりません。`winget install Gyan.FFmpeg`で入れてから、アプリを再起動してください。", true);
    else if (atLimit) showNotice(`登録できる編集動画の上限（${data.limit}件）に達しています。不要なものを削除してください。`, true);
    else if (notice.classList.contains("ve-error")) showNotice("");
    listEl.replaceChildren(...(data.edits.length ? data.edits.map(item) : [Object.assign(document.createElement("li"), { className: "hint ve-empty", textContent: "まだありません" })]));
    // 処理が終わったものを知らせる
    data.edits.forEach((e) => {
      const before = known[e.id];
      if (before && (before === "queued" || before === "running") && e.status === "done") window.showToast(`編集が完了しました: ${e.summary}`, "success");
      if (before && (before === "queued" || before === "running") && e.status === "failed") window.showToast(`編集に失敗しました: ${e.summary}`, "error");
      known[e.id] = e.status;
    });
    refresh();
    if (data.edits.some((e) => e.status === "queued" || e.status === "running")) timer = setTimeout(load, 2500);
  }

  load();
})();
