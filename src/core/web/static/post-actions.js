// 詳細画面: 操作メニューの開閉、投稿予定の保存、手動で投稿した記録(投稿済みとして記録・取り消し)。
(function () {
  const root = document.getElementById("asset-root");
  if (!root) return;
  const assetId = root.dataset.assetId;

  // --- 操作メニュー: 外側のクリック・項目の選択で閉じる ---
  const menu = document.getElementById("asset-menu");
  if (menu) {
    document.addEventListener("click", (e) => {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    });
    menu.addEventListener("click", (e) => {
      // 項目を選んだら閉じる(説明アイコンのクリックは、閉じない)
      if (e.target.closest(".menu-item") && !e.target.closest(".info-icon")) {
        setTimeout(() => { menu.open = false; }, 0);
      }
    });
  }

  // --- 投稿予定: チェックを入れ外しした瞬間に保存する ---
  const rows = document.querySelector(".post-rows");
  const note = document.getElementById("no-plan-note");
  if (rows) {
    rows.addEventListener("change", async (e) => {
      if (!e.target.matches('input[name="channel"]')) return;
      const body = new URLSearchParams();
      rows.querySelectorAll('input[name="channel"]:checked').forEach((c) => body.append("channel", c.value));
      try {
        const res = await fetch(`/assets/${assetId}/channels`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest" },
          body,
        });
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        if (note) note.hidden = !data.no_plan;
        window.showToast(data.no_plan ? "投稿予定なしにしました" : "投稿予定を保存しました", "success");
      } catch (err) {
        e.target.checked = !e.target.checked; // 失敗したら、表示を元に戻す
        window.showToast("投稿予定を保存できませんでした", "error");
      }
    });
  }

  // --- 手動で投稿した記録 ---
  const dialog = document.getElementById("manual-post-dialog");
  let channel = null;

  function localInputValue(date) {
    const p = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`;
  }

  document.addEventListener("click", async (e) => {
    const manual = e.target.closest("[data-post-manual]");
    if (manual && dialog) {
      channel = manual.dataset.postManual;
      document.getElementById("manual-post-label").textContent = `（${manual.dataset.label}）`;
      document.getElementById("manual-post-time").value = localInputValue(new Date());
      document.getElementById("manual-post-url").value = "";
      dialog.showModal();
      return;
    }
    const clear = e.target.closest("[data-post-clear]");
    if (clear) {
      const ok = await window.confirmDialog(
        `${clear.dataset.label}の投稿の記録を取り消して、未投稿に戻します。よろしいですか？（実際の投稿が消えるわけではありません）`
      );
      if (!ok) return;
      const res = await fetch(`/api/assets/${assetId}/posts/${clear.dataset.postClear}/clear`, { method: "POST" });
      if (res.ok) {
        window.showToast("投稿の記録を取り消しました", "success");
        setTimeout(() => window.location.reload(), 500);
      } else {
        window.showToast("取り消せませんでした", "error");
      }
    }
  });

  if (dialog) {
    document.getElementById("manual-post-cancel").addEventListener("click", () => dialog.close());
    document.getElementById("manual-post-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const value = document.getElementById("manual-post-time").value;
      const body = {
        posted_at: value ? new Date(value).toISOString() : null,
        url: document.getElementById("manual-post-url").value.trim(),
      };
      const res = await fetch(`/api/assets/${assetId}/posts/${channel}/manual`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showToast(data.error || "記録できませんでした", "error");
        return;
      }
      dialog.close();
      window.showToast("投稿済みとして記録しました", "success");
      setTimeout(() => window.location.reload(), 500);
    });
  }
})();
