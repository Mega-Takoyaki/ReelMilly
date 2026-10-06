// ごみ箱画面: サムネイルから複数選択して、元に戻す。
(function () {
  const checkboxes = Array.from(document.querySelectorAll(".asset-checkbox"));
  const toolbar = document.getElementById("trash-toolbar");
  const countEl = document.getElementById("trash-count");
  const toggle = document.getElementById("select-all-toggle");
  if (!toolbar) return;

  const selected = new Set();

  function refresh() {
    toolbar.hidden = selected.size === 0;
    countEl.textContent = `${selected.size}件を選択中`;
  }

  checkboxes.forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
    cb.addEventListener("change", () => {
      if (cb.checked) selected.add(cb.value);
      else selected.delete(cb.value);
      refresh();
    });
  });

  if (toggle) {
    toggle.addEventListener("click", () => {
      const all = selected.size < checkboxes.length;
      checkboxes.forEach((cb) => {
        cb.checked = all;
        if (all) selected.add(cb.value);
        else selected.delete(cb.value);
      });
      toggle.textContent = all ? "すべて選択を解除" : "すべて選択";
      refresh();
    });
  }

  document.getElementById("trash-clear").addEventListener("click", () => {
    selected.clear();
    checkboxes.forEach((cb) => (cb.checked = false));
    refresh();
  });

  async function purge(body, message) {
    const ok = await window.confirmDialog(message);
    if (!ok) return;
    try {
      const res = await fetch("/api/assets/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);
      if (data.deleted > 0) window.showToast(`${data.deleted}件を完全に削除しました`, "success");
      (data.errors || []).forEach((m) => window.showToast(m, "error"));
      setTimeout(() => window.location.reload(), data.errors && data.errors.length ? 2500 : 600);
    } catch (err) {
      window.showToast(`削除できませんでした: ${err.message}`, "error");
    }
  }

  document.getElementById("trash-purge").addEventListener("click", () => {
    if (selected.size === 0) return;
    purge(
      { asset_ids: Array.from(selected) },
      `選択した${selected.size}件を完全に削除します。ファイルと記録が消え、元に戻せません。よろしいですか？`
    );
  });

  const emptyButton = document.getElementById("trash-empty");
  if (emptyButton) {
    emptyButton.addEventListener("click", () => {
      purge(
        { all: true },
        `ごみ箱の${checkboxes.length}件すべてを完全に削除します。ファイルと記録が消え、元に戻せません。よろしいですか？`
      );
    });
  }

  document.getElementById("trash-restore").addEventListener("click", async () => {
    if (selected.size === 0) return;
    try {
      const res = await fetch("/api/assets/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: Array.from(selected) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);
      window.showToast(`${data.restored}件を元に戻しました`, "success");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      window.showToast(`元に戻せませんでした: ${err.message}`, "error");
    }
  });
})();
