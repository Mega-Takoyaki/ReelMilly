// 一覧・ごみ箱: 下へスクロールすると、続きの作品を読み込んで、全件を見られるようにする。
// - 末尾の目印(#grid-more)が画面に近づくと、/?offset=N&partial=cards(今の絞り込みのまま)で続きを取得して追加する
// - 絞り込みで一覧が差し替わると目印も新しくなるので、そのたびに監視し直す
// - 追加したときは "grid-appended" を通知する(プレビュー・AI処理の表示などが、増えた分を扱えるように)
(function () {
  let observer = null;
  let loading = false;

  function updateCount(shown, total) {
    const el = document.getElementById("grid-count");
    if (!el) return;
    const unit = location.pathname === "/trash" ? "件" : "件を表示中";
    el.dataset.total = String(total);
    el.textContent = shown < total ? `${shown}${unit}（全${total}件）` : `${shown}${unit}`;
  }

  async function loadMore() {
    const more = document.getElementById("grid-more");
    const grid = document.querySelector(".asset-grid");
    if (!more || !grid || loading) return;
    loading = true;
    try {
      const params = new URLSearchParams(location.search);
      params.set("offset", more.dataset.next);
      params.set("partial", "cards");
      const res = await fetch(`${location.pathname}?${params}`, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      const tpl = document.createElement("template");
      tpl.innerHTML = data.html;
      const known = new Set(Array.from(grid.querySelectorAll(".asset-card")).map((c) => c.dataset.assetId));
      const incoming = Array.from(tpl.content.children);
      incoming.forEach((card) => {
        // 読み込んでいる間に、並びが変わって(新しい作品が入って)同じ作品が来ることがあるので、重ねない
        if (!card.dataset.assetId || !known.has(card.dataset.assetId)) grid.appendChild(card);
      });
      const shown = grid.querySelectorAll(".asset-card").length;
      updateCount(shown, data.total);
      if (data.next >= data.total || incoming.length === 0) {
        more.remove();
      } else {
        more.dataset.next = String(data.next);
      }
      window.dispatchEvent(new Event("grid-appended"));
    } catch (err) {
      if (window.showToast) window.showToast("続きを読み込めませんでした。少し待って、もう一度スクロールしてください", "error");
    } finally {
      loading = false;
      watch(); // まだ目印が見えていれば、続けて読み込む
    }
  }

  function watch() {
    if (observer) observer.disconnect();
    const more = document.getElementById("grid-more");
    if (!more) return;
    observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    }, { rootMargin: "900px 0px" });
    observer.observe(more);
  }

  watch();
  window.addEventListener("grid-updated", watch);
})();
