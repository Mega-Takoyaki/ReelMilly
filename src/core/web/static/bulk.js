(function () {
  const checkboxes = Array.from(document.querySelectorAll(".asset-checkbox"));
  const toolbar = document.getElementById("bulk-toolbar");
  const countEl = document.getElementById("bulk-count");

  if (checkboxes.length === 0 || !toolbar) return;

  const selected = new Set();

  function refreshToolbar() {
    if (selected.size === 0) {
      toolbar.hidden = true;
      return;
    }
    toolbar.hidden = false;
    countEl.textContent = `${selected.size}件を選択中`;
  }

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

  document.getElementById("bulk-clear").addEventListener("click", () => {
    selected.clear();
    checkboxes.forEach((cb) => (cb.checked = false));
    refreshToolbar();
  });

  document.getElementById("bulk-tag-apply").addEventListener("click", async () => {
    const tagName = document.getElementById("bulk-tag-input").value.trim();
    if (!tagName || selected.size === 0) return;
    try {
      await postJson("/assets/bulk/tag", {
        asset_ids: Array.from(selected),
        tag_name: tagName,
      });
      window.location.reload();
    } catch (err) {
      alert(`タグ付与に失敗しました: ${err.message}`);
    }
  });

  document.getElementById("bulk-folder-apply").addEventListener("click", async () => {
    const folderId = document.getElementById("bulk-folder-select").value;
    if (!folderId || selected.size === 0) return;
    try {
      await postJson("/assets/bulk/folder", {
        asset_ids: Array.from(selected),
        folder_id: Number(folderId),
      });
      window.location.reload();
    } catch (err) {
      alert(`フォルダ追加に失敗しました: ${err.message}`);
    }
  });

  document.getElementById("bulk-rating-apply").addEventListener("click", async () => {
    const rating = document.getElementById("bulk-rating-select").value;
    if (!rating || selected.size === 0) return;
    if (!confirm(`選択した${selected.size}件を "${rating}" として承認します。よろしいですか？`)) {
      return;
    }
    try {
      await postJson("/assets/bulk/confirm", {
        asset_ids: Array.from(selected),
        content_rating: rating,
      });
      window.location.reload();
    } catch (err) {
      alert(`一括承認に失敗しました: ${err.message}`);
    }
  });
})();
