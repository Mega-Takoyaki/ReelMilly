// 透かしの設定ダイアログ(一覧の一括操作・詳細画面で共用)。
// 文字・挿入位置・濃さ・大きさを実行前に指定し、非同期ジョブとして積む。
(function () {
  const dialog = document.getElementById("wm-dialog");
  if (!dialog) return;

  const text = document.getElementById("wm-text");
  const opacity = document.getElementById("wm-opacity");
  const size = document.getElementById("wm-size");
  const opacityOut = document.getElementById("wm-opacity-out");
  const sizeOut = document.getElementById("wm-size-out");
  const target = document.getElementById("wm-target");
  const preview = document.getElementById("wm-preview-img");
  const runButton = document.getElementById("wm-run");
  const defaults = JSON.parse(dialog.dataset.defaults || "{}");

  let assetIds = [];
  let previewId = null;
  let timer = null;

  function position() {
    const checked = dialog.querySelector('input[name="wm-position"]:checked');
    return checked ? checked.value : "bottom-right";
  }

  function params() {
    return { text: text.value.trim(), position: position(), opacity: Number(opacity.value), size: Number(size.value) };
  }

  function updatePreview() {
    opacityOut.textContent = ` ${opacity.value}%`;
    sizeOut.textContent = ` ${Number(size.value).toFixed(1)}%`;
    clearTimeout(timer);
    if (!previewId || !text.value.trim()) {
      preview.hidden = true;
      return;
    }
    timer = setTimeout(() => {
      const p = params();
      const qs = new URLSearchParams({ text: p.text, position: p.position, opacity: p.opacity, size: p.size });
      preview.src = `/assets/${previewId}/watermark-preview?${qs}`;
      preview.hidden = false;
    }, 250);
  }

  window.openWatermarkDialog = function (ids, previewAssetId) {
    assetIds = ids;
    previewId = previewAssetId || ids[0];
    text.value = defaults.text || "";
    opacity.value = defaults.opacity;
    size.value = defaults.size;
    const radio = dialog.querySelector(`input[name="wm-position"][value="${defaults.position}"]`);
    (radio || dialog.querySelector('input[name="wm-position"][value="bottom-right"]')).checked = true;
    target.textContent = ids.length > 1 ? `${ids.length}件の作品に挿入します（画像のみ）` : "この作品に挿入します";
    dialog.showModal();
    updatePreview();
    text.focus();
  };

  [text, opacity, size].forEach((el) => el.addEventListener("input", updatePreview));
  dialog.querySelectorAll('input[name="wm-position"]').forEach((r) => r.addEventListener("change", updatePreview));
  document.getElementById("wm-cancel").addEventListener("click", () => dialog.close());

  document.getElementById("wm-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const p = params();
    if (!p.text) {
      text.focus();
      return;
    }
    runButton.disabled = true;
    try {
      const res = await fetch("/api/watermark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: assetIds, ...p }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);
      Object.assign(defaults, p); // 次に開いたときの初期値
      dialog.close();
      let message = data.queued > 0
        ? `透かしの挿入を${data.queued}件キューに追加しました。完了したらお知らせします`
        : "透かしの挿入は既に待機中/実行中です";
      if (data.skipped_video > 0) message += `（動画${data.skipped_video}件は未対応のためスキップ）`;
      window.showToast(message, "success");
      if (!data.status.worker_alive) {
        window.showToast("ワーカーが停止中です。別のターミナルで reelmilly watch を起動してください", "error");
      }
      if (window.aiLive) window.aiLive.track(assetIds, ["watermark"]);
    } catch (err) {
      window.showToast(`透かしの挿入を開始できませんでした: ${err.message}`, "error");
    } finally {
      runButton.disabled = false;
    }
  });

  // 詳細画面のボタン
  const open = document.getElementById("wm-open");
  const root = document.getElementById("asset-root");
  if (open && root) open.addEventListener("click", () => window.openWatermarkDialog([root.dataset.assetId], root.dataset.assetId));
  const clear = document.getElementById("wm-clear");
  if (clear && root) {
    clear.addEventListener("click", async () => {
      const ok = await window.confirmDialog("この作品の透かしを外します（元のファイルに戻ります）。よろしいですか？");
      if (!ok) return;
      const res = await fetch("/api/watermark/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: [root.dataset.assetId] }),
      });
      if (res.ok) {
        window.showToast("透かしを外しました", "success");
        setTimeout(() => window.location.reload(), 500);
      } else {
        window.showToast("透かしを外せませんでした", "error");
      }
    });
  }
})();
