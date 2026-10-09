// 作品詳細画面の「ファイル(原本と加工版)」: 投稿・予約、AI判定、区分、削除と、処理中の自動更新。
// 一覧(#live-library)は、サーバーで描くため、操作のあとは、ページから取り直して差し替える。
// 処理中(待機中・実行中の加工版、AI判定中)があるあいだだけ、数秒おきに取り直す。
(function () {
  const root = () => document.getElementById("live-library");
  if (!root()) return;
  const assetId = root().dataset.assetId;
  let timer = null;

  async function refresh(keepPolling) {
    clearTimeout(timer);
    try {
      const res = await fetch(window.location.href, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const fresh = doc.getElementById("live-library");
      const current = root();
      if (fresh && current) {
        // 開いているメニューは、更新で閉じてしまわないよう、開いている間は、差し替えを見送る
        if (!current.querySelector("details[open]")) current.innerHTML = fresh.innerHTML;
        current.dataset.originalRating = fresh.dataset.originalRating || "";
      }
    } catch (err) {
      /* 次の機会に、取り直す */
    }
    const busy = root() && root().querySelector(".lib-busy");
    if (busy || keepPolling === true) timer = setTimeout(() => refresh(false), 3000);
  }
  window.refreshLibrary = refresh;
  if (root().querySelector(".lib-busy")) timer = setTimeout(() => refresh(false), 3000);

  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `失敗しました (${res.status})`);
    return data;
  }
  const closeMenus = () => root().querySelectorAll("details[open]").forEach((d) => { d.open = false; });
  const RATING_NOTE = { sfw: "sfw", suggestive: "suggestive", explicit: "explicit" };

  document.addEventListener("click", async (e) => {
    const box = root();
    if (!box || !box.contains(e.target)) return;

    // 投稿・予約: このファイルを選んだ状態で、ダイアログを開く
    const postBtn = e.target.closest("[data-lib-post]");
    if (postBtn) {
      if (window.openPostNow) window.openPostNow([assetId], { [assetId]: postBtn.dataset.libPost });
      return;
    }

    // 加工版のsfw/nsfw判定
    const ai = e.target.closest("[data-lib-ai]");
    if (ai) {
      closeMenus();
      try {
        const data = await post(`/api/assets/${assetId}/versions/${encodeURIComponent(ai.dataset.libAi)}/nsfw`);
        window.showToast(data.queued ? "AI判定を登録しました。終わると、一覧に出ます" : "すでに、判定の待機中・実行中です", "success");
        refresh(true);
      } catch (err) {
        window.showToast(err.message, "error");
      }
      return;
    }

    // 加工版の区分(このファイルだけ。空欄＝原本に合わせる)
    const rating = e.target.closest("[data-lib-rating]");
    if (rating) {
      closeMenus();
      const value = rating.dataset.value || null;
      const original = box.dataset.originalRating || null;
      const order = { sfw: 0, suggestive: 1, explicit: 2 };
      // 原本より、ゆるい区分にするときは、確認する(隠すべき部分が、本当に隠れているか)
      if (value && original && order[value] < order[original]) {
        const ok = await window.confirmDialog(
          `原本は「${original}」です。この加工版を「${value}」にします。\n見せたくない部分は、切り落とし・ぼかしなどで、本当に写っていませんか？（投稿の既定値などに、この区分を使います）`
        );
        if (!ok) return;
      }
      try {
        await post(`/api/assets/${assetId}/versions/${encodeURIComponent(rating.dataset.libRating)}/rating`, { rating: value });
        window.showToast(value ? `この加工版の区分を「${RATING_NOTE[value]}」にしました` : "原本の区分に合わせました", "success");
        refresh(false);
      } catch (err) {
        window.showToast(err.message, "error");
      }
      return;
    }

    // 加工版(編集した動画)の削除
    const del = e.target.closest("[data-lib-delete-edit]");
    if (del) {
      const ok = await window.confirmDialog(`加工版「${del.dataset.label}」を削除します（原本は変わりません）。よろしいですか？`);
      if (!ok) return;
      const res = await fetch(`/api/assets/${assetId}/edits/${del.dataset.libDeleteEdit}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) window.showToast(data.error || "削除できませんでした", "error");
      refresh(false);
    }
  });

  // 開いたメニューは、外側を押したら閉じる
  document.addEventListener("click", (e) => {
    const box = root();
    if (!box) return;
    box.querySelectorAll("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  });
})();
