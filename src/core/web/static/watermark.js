// 透かしの設定ダイアログ(一覧の一括操作・詳細画面で共用)と、位置選択(5x5・複数)の操作。
// 文字・挿入位置・濃さ・大きさを実行前に指定し、非同期ジョブとして積む。
// ダイアログの初期値は、この画面(タブ)を開いている間は前回の設定を引き継ぎ、最初は設定画面の既定値。
(function () {
  // --- 位置選択(ダイアログと設定画面で共用): 「25か所すべて選択」「すべて解除」 ---
  document.querySelectorAll(".wm-picker").forEach((picker) => {
    const cells = () => picker.querySelectorAll(".wm-grid5 input[type=checkbox]");
    picker.querySelector("[data-wm-all]").addEventListener("click", () => {
      cells().forEach((c) => (c.checked = true));
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });
    picker.querySelector("[data-wm-none]").addEventListener("click", () => {
      picker.querySelectorAll("input[type=checkbox]").forEach((c) => (c.checked = false));
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  const dialog = document.getElementById("wm-dialog");
  if (!dialog) return;

  const SESSION_KEY = "reelmilly:wm-last";
  const text = document.getElementById("wm-text");
  const opacity = document.getElementById("wm-opacity");
  const size = document.getElementById("wm-size");
  const opacityOut = document.getElementById("wm-opacity-out");
  const sizeOut = document.getElementById("wm-size-out");
  const target = document.getElementById("wm-target");
  const preview = document.getElementById("wm-preview-img");
  const runButton = document.getElementById("wm-run");
  const picker = dialog.querySelector(".wm-picker");
  const baseDefaults = JSON.parse(dialog.dataset.defaults || "{}");

  let assetIds = [];
  let previewId = null;
  let timer = null;

  function lastValues() {
    try {
      return { ...baseDefaults, ...JSON.parse(sessionStorage.getItem(SESSION_KEY) || "{}") };
    } catch (e) {
      return { ...baseDefaults };
    }
  }

  function positions() {
    return Array.from(picker.querySelectorAll("input[type=checkbox]:checked")).map((c) => c.value);
  }

  function params() {
    return { text: text.value.trim(), positions: positions(), opacity: Number(opacity.value), size: Number(size.value) };
  }

  function updatePreview() {
    opacityOut.textContent = ` ${opacity.value}%`;
    sizeOut.textContent = ` ${Number(size.value).toFixed(1)}%`;
    clearTimeout(timer);
    const p = params();
    if (!previewId || !p.text || p.positions.length === 0) {
      preview.hidden = true;
      return;
    }
    timer = setTimeout(() => {
      const qs = new URLSearchParams({ text: p.text, opacity: p.opacity, size: p.size });
      p.positions.forEach((pos) => qs.append("position", pos));
      preview.src = `/assets/${previewId}/watermark-preview?${qs}`;
      preview.hidden = false;
    }, 250);
  }

  window.openWatermarkDialog = function (ids, previewAssetId) {
    assetIds = ids;
    previewId = previewAssetId || ids[0];
    const v = lastValues();
    text.value = v.text || "";
    opacity.value = v.opacity;
    size.value = v.size;
    const chosen = new Set(v.positions || []);
    picker.querySelectorAll("input[type=checkbox]").forEach((c) => (c.checked = chosen.has(c.value)));
    target.textContent = ids.length > 1 ? `${ids.length}件の作品に挿入します（画像のみ）` : "この作品に挿入します";
    dialog.showModal();
    updatePreview();
    text.focus();
  };

  [text, opacity, size].forEach((el) => el.addEventListener("input", updatePreview));
  picker.addEventListener("change", updatePreview);
  document.getElementById("wm-cancel").addEventListener("click", () => dialog.close());

  document.getElementById("wm-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const p = params();
    if (!p.text) {
      text.focus();
      return;
    }
    if (p.positions.length === 0) {
      window.showToast("挿入位置を1か所以上選んでください", "error");
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
      try {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify(p)); // 次に開いたときの初期値(この画面を開いている間)
      } catch (err) { /* 保存できない環境では既定値に戻るだけ */ }
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

  // 詳細画面のメニュー(中身は完了時に差し替わるため、documentで受ける)
  function closeMenu() {
    const menu = document.getElementById("asset-menu");
    if (menu) menu.open = false;
  }

  document.addEventListener("click", async (e) => {
    const root = document.getElementById("asset-root");
    if (!root) return;

    if (e.target.closest("#wm-open")) {
      closeMenu();
      window.openWatermarkDialog([root.dataset.assetId], root.dataset.assetId);
      return;
    }

    if (e.target.closest("#wm-clear")) {
      closeMenu();
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
      return;
    }

    // 「透かし入りを見る」: 一覧のプレビューと同じポップアップで表示する
    const view = e.target.closest("[data-wm-view]");
    if (view) {
      e.preventDefault();
      closeMenu();
      if (window.openPreview) window.openPreview(view.dataset.src, "image", "透かし入り");
    }
  });
})();
