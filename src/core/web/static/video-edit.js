// 加工エディタ(切り出し)のポップアップ。作品詳細画面の「ファイル(原本と加工版)」の「＋加工版を作る」から開く。
// - 開始・終了は、秒の数値か「現在位置を開始/終了に」で指定する(時間軸のクリックでその位置へ移動できる)
// - 切り出しは非同期ジョブ。登録したら、ポップアップを閉じて、ファイル一覧に「待機中」で並ぶ(一覧が、完了まで自動で更新される)
(function () {
  const dialog = document.getElementById("edit-dialog");
  if (!dialog) return;
  const assetId = dialog.dataset.assetId;
  const $ = (id) => document.getElementById(id);

  const video = $("ve-video");
  const startInput = $("ve-start");
  const endInput = $("ve-end");
  const lengthEl = $("ve-length");
  const timeline = $("ve-timeline");
  const rangeEl = $("ve-range");
  const playhead = $("ve-playhead");
  const runButton = $("ve-run");
  const previewButton = $("ve-preview");
  const notice = $("ve-notice");

  let duration = 0;
  let previewEnd = null;
  let atLimit = false;

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
      setValue(startInput, 0);
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
  $("ve-set-start").addEventListener("click", () => setValue(startInput, video.currentTime));
  $("ve-set-end").addEventListener("click", () => setValue(endInput, video.currentTime));
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

  function close() {
    video.pause();
    dialog.close();
  }
  $("ve-close").addEventListener("click", close);
  $("ve-cancel").addEventListener("click", close);
  dialog.addEventListener("close", () => video.pause());

  // 開く: 動画を読み込み(開くまでは、読み込まない)、登録の上限・ffmpegの有無を確かめる
  window.openEditor = async function (source, from) {
    source = source || "original";
    showNotice("");
    dialog.dataset.source = source;
    if (video.dataset.source !== source) {
      video.src = window.versionUrl(assetId, source);
      video.dataset.source = source;
    }
    video.preload = "metadata";
    video.load();
    atLimit = false;
    try {
      const data = await (await fetch(`/api/assets/${assetId}/edits`)).json();
      atLimit = data.used >= data.limit;
      if (!data.ffmpeg) showNotice("ffmpegが見つかりません。`winget install Gyan.FFmpeg`で入れてから、アプリを再起動してください。", true);
      else if (atLimit) showNotice(`登録できる加工版の上限（${data.limit}件）に達しています。不要なものを削除してください。`, true);
    } catch (err) {
      /* 登録のときに、サーバーが検査する */
    }
    refresh();
    if (!dialog.open) dialog.showModal();
  };

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-editor]");
    if (!opener) return;
    const menu = opener.closest("details");
    if (menu) menu.open = false;
    window.openEditor(opener.dataset.source, opener.dataset.from);
  });

  runButton.addEventListener("click", async () => {
    const mode = (dialog.querySelector('input[name="ve-mode"]:checked') || {}).value || "accurate";
    runButton.disabled = true;
    showNotice("");
    try {
      const res = await fetch(`/api/assets/${assetId}/edits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "trim", source: dialog.dataset.source || "original", start: num(startInput), end: num(endInput), mode }),
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
      refresh();
    }
  });
})();
