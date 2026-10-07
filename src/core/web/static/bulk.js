(function () {
  const toolbar = document.getElementById("bulk-toolbar");
  const countEl = document.getElementById("bulk-count");
  let checkboxes = [];

  if (!toolbar) return;

  const selected = new Set();

  function refreshToolbar() {
    if (selected.size === 0) {
      toolbar.hidden = true;
      return;
    }
    toolbar.hidden = false;
    countEl.textContent = `${selected.size}件を選択中`;
  }

  // 一覧のサムネイル・「表示中をすべて選択」を結び付ける。絞り込みで一覧が差し替わるたびに呼ぶ
  function bindGrid() {
    checkboxes = Array.from(document.querySelectorAll(".asset-checkbox"));
    checkboxes.forEach((checkbox) => {
      checkbox.addEventListener("click", (e) => e.stopPropagation());
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          selected.add(checkbox.value);
        } else {
          selected.delete(checkbox.value);
        }
        refreshToolbar();
      });
    });

    const selectAllToggle = document.getElementById("select-all-toggle");
    if (selectAllToggle) {
      selectAllToggle.addEventListener("click", () => {
        const shouldSelectAll = selected.size < checkboxes.length;
        checkboxes.forEach((checkbox) => {
          checkbox.checked = shouldSelectAll;
          if (shouldSelectAll) {
            selected.add(checkbox.value);
          } else {
            selected.delete(checkbox.value);
          }
        });
        selectAllToggle.textContent = shouldSelectAll ? "表示中の選択を解除" : "表示中をすべて選択";
        refreshToolbar();
      });
    }
  }

  // スマホでは、一括操作のバーを「件数 + 操作▾」だけに畳んでおき、必要なときに開く
  const bulkToggle = document.getElementById("bulk-toggle");
  if (bulkToggle) {
    bulkToggle.addEventListener("click", () => {
      const open = toolbar.classList.toggle("open");
      bulkToggle.textContent = open ? "操作 ▴" : "操作 ▾";
      bulkToggle.setAttribute("aria-expanded", String(open));
    });
  }

  bindGrid();
  window.addEventListener("grid-updated", () => {
    selected.clear(); // 表示する作品が変わるので、選択はリセットする
    refreshToolbar();
    bindGrid();
  });

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `request failed: ${response.status}`);
    }
    return data;
  }

  [["bulk-noplan", false, "投稿予定なしにしました"], ["bulk-plan", true, "投稿予定に戻しました"]].forEach(([id, planned, message]) => {
    document.getElementById(id).addEventListener("click", async () => {
      if (selected.size === 0) return;
      try {
        const data = await postJson("/api/assets/post-plan", { asset_ids: Array.from(selected), planned });
        window.showToast(`${data.updated}件を${message}`, "success");
        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        window.showToast(`変更に失敗しました: ${err.message}`, "error");
      }
    });
  });

  // 破綻画像(キメラ)のマーク。付けた作品は既定の一覧から消える(フィルタで表示できる)
  [["bulk-broken", true], ["bulk-unbroken", false]].forEach(([id, broken]) => {
    document.getElementById(id).addEventListener("click", async () => {
      if (selected.size === 0) return;
      if (broken) {
        const ok = await window.confirmDialog(
          `選択した${selected.size}件を破綻画像（キメラ）にします。既定の一覧に表示されなくなり、SNS投稿の対象からも外れます（フィルタの「破綻画像」で表示できます）。よろしいですか？`
        );
        if (!ok) return;
      }
      try {
        const data = await postJson("/api/assets/broken", { asset_ids: Array.from(selected), broken });
        window.showToast(
          broken ? `${data.changed}件を破綻画像にしました` : `${data.changed}件の破綻画像のマークを解除しました`,
          "success"
        );
        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        window.showToast(`変更に失敗しました: ${err.message}`, "error");
      }
    });
  });

  // 透かし: 設定ダイアログを開く(プレビューには、選択した画像のうち最初の1枚を使う)
  document.getElementById("bulk-wm").addEventListener("click", () => {
    if (selected.size === 0) return;
    const ids = Array.from(selected);
    const firstImage = ids.find((id) => {
      const card = document.querySelector(`.asset-card[data-asset-id="${id}"] .asset-media`);
      return card && card.dataset.kind === "image";
    });
    if (!firstImage) {
      window.showToast("透かしを入れられるのは画像だけです（動画は未対応です）", "error");
      return;
    }
    window.openWatermarkDialog(ids, firstImage);
  });

  document.getElementById("bulk-wm-clear").addEventListener("click", async () => {
    if (selected.size === 0) return;
    const ok = await window.confirmDialog(`選択した${selected.size}件の透かしを外します（元のファイルに戻ります）。よろしいですか？`);
    if (!ok) return;
    try {
      const data = await postJson("/api/watermark/clear", { asset_ids: Array.from(selected) });
      window.showToast(`${data.cleared}件の透かしを外しました`, "success");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      window.showToast(`透かしを外せませんでした: ${err.message}`, "error");
    }
  });

  document.getElementById("bulk-trash").addEventListener("click", async () => {
    if (selected.size === 0) return;
    const ok = await window.confirmDialog(
      `選択した${selected.size}件をごみ箱へ移動します。よろしいですか？（ごみ箱から元に戻せます）`
    );
    if (!ok) return;
    try {
      const data = await postJson("/api/assets/trash", { asset_ids: Array.from(selected) });
      window.showToast(`${data.moved}件をごみ箱へ移動しました`, "success");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      window.showToast(`ごみ箱へ移動できませんでした: ${err.message}`, "error");
    }
  });

  document.getElementById("bulk-clear").addEventListener("click", () => {
    selected.clear();
    checkboxes.forEach((cb) => (cb.checked = false));
    const toggle = document.getElementById("select-all-toggle");
    if (toggle) toggle.textContent = "表示中をすべて選択";
    refreshToolbar();
  });

  document.getElementById("bulk-tag-apply").addEventListener("click", async () => {
    const tagName = document.getElementById("bulk-tag-input").value.trim();
    if (!tagName || selected.size === 0) return;
    try {
      const data = await postJson("/assets/bulk/tag", {
        asset_ids: Array.from(selected),
        tag_name: tagName,
      });
      window.showToast(`${data.updated}件にタグ「${tagName}」を付与しました`, "success");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      window.showToast(`タグ付与に失敗しました: ${err.message}`, "error");
    }
  });

  document.getElementById("bulk-folder-apply").addEventListener("click", async () => {
    const folderId = document.getElementById("bulk-folder-select").value;
    if (!folderId || selected.size === 0) return;
    try {
      const data = await postJson("/assets/bulk/folder", {
        asset_ids: Array.from(selected),
        folder_id: Number(folderId),
      });
      window.showToast(`${data.updated}件をフォルダに追加しました`, "success");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      window.showToast(`フォルダ追加に失敗しました: ${err.message}`, "error");
    }
  });

  [["bulk-ai-nsfw", "nsfw"], ["bulk-ai-describe", "describe"]].forEach(([id, kind]) => {
    document.getElementById(id).addEventListener("click", () => {
      if (selected.size === 0 || !window.aiLive) return;
      window.aiLive.enqueue(Array.from(selected), kind);
    });
  });

  document.getElementById("bulk-rating-apply").addEventListener("click", async () => {
    const rating = document.getElementById("bulk-rating-select").value;
    if (!rating || selected.size === 0) return;
    const confirmed = await window.confirmDialog(
      `選択した${selected.size}件を "${rating}" として承認します。よろしいですか？`
    );
    if (!confirmed) return;
    try {
      const data = await postJson("/assets/bulk/confirm", {
        asset_ids: Array.from(selected),
        content_rating: rating,
      });
      window.showToast(`${data.updated}件を"${rating}"として承認しました`, "success");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      window.showToast(`一括承認に失敗しました: ${err.message}`, "error");
    }
  });
})();
