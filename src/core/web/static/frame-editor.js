// 加工エディタ(静止画の取り出し)のポップアップ。動画を止めた位置の1コマを、jpg/pngの加工版にする。
(function () {
  const dialog = document.getElementById("frame-dialog");
  if (!dialog) return;
  const assetId = dialog.dataset.assetId;
  const $ = (id) => document.getElementById(id);
  const video = $("fr-video");
  const seek = $("fr-seek");
  const notice = $("fr-notice");
  const FRAME = 1 / 25;  // 1コマの目安(動画の実際のコマ率は、読み取れないため)

  const format = () => (dialog.querySelector('input[name="fr-format"]:checked') || {}).value || "jpg";
  const scale = () => parseInt($("fr-scale").value, 10) || 1;

  function showNotice(text, isError) {
    notice.hidden = !text;
    notice.textContent = text || "";
    notice.classList.toggle("ve-error", !!isError);
  }
  function refresh() {
    $("fr-time").textContent = `${(video.currentTime || 0).toFixed(2)} / ${(video.duration || 0).toFixed(2)} 秒`;
    if (video.duration) seek.value = String(Math.round((video.currentTime / video.duration) * 1000));
    const w = video.videoWidth, h = video.videoHeight;
    $("fr-size").textContent = w ? `出力: ${w * scale()}×${h * scale()}` : "";
  }
  const stepBy = (delta) => {
    video.pause();
    video.currentTime = Math.min(Math.max((video.currentTime || 0) + delta, 0), video.duration || 0);
  };

  ["loadedmetadata", "timeupdate", "seeked"].forEach((n) => video.addEventListener(n, refresh));
  video.addEventListener("play", () => { $("fr-play").textContent = "⏸ 一時停止"; });
  video.addEventListener("pause", () => { $("fr-play").textContent = "▶ 再生"; });
  $("fr-play").addEventListener("click", () => { video.paused ? video.play().catch(() => {}) : video.pause(); });
  $("fr-prev").addEventListener("click", () => stepBy(-FRAME));
  $("fr-next").addEventListener("click", () => stepBy(FRAME));
  seek.addEventListener("input", () => { if (video.duration) { video.pause(); video.currentTime = (seek.value / 1000) * video.duration; } });
  $("fr-scale").addEventListener("change", refresh);

  function close() {
    video.pause();
    dialog.close();
  }
  $("fr-close").addEventListener("click", close);
  $("fr-cancel").addEventListener("click", close);

  window.openFrame = function (source) {
    source = source || "original";
    showNotice("");
    dialog.dataset.source = source;
    if (video.dataset.source !== source) {
      video.src = `${window.versionUrl(assetId, source)}#t=0.1`;
      video.dataset.source = source;
    }
    video.preload = "metadata";
    video.load();
    refresh();
    if (!dialog.open) dialog.showModal();
  };
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-frame]");
    if (!opener) return;
    const menu = opener.closest("details");
    if (menu) menu.open = false;
    window.openFrame(opener.dataset.source);
  });

  $("fr-run").addEventListener("click", async () => {
    $("fr-run").disabled = true;
    showNotice("");
    video.pause();
    try {
      const res = await fetch(`/api/assets/${assetId}/edits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "frame", source: dialog.dataset.source || "original", time: video.currentTime, format: format(), scale: scale() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `登録できませんでした (${res.status})`);
      close();
      window.showToast("静止画の作成を登録しました。終わると、ファイル一覧と通知に出ます", "success");
      if (window.refreshLibrary) window.refreshLibrary(true);
    } catch (err) {
      showNotice(err.message, true);
      window.showToast(err.message, "error");
    } finally {
      $("fr-run").disabled = false;
    }
  });
})();
