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
