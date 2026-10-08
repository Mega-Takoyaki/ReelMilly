// 今すぐ投稿: 一覧(選択した作品)・詳細画面(その作品)から、作品とバージョンを選んで、いますぐ1つの投稿として投稿する。
// - window.openPostNow(assetIds) でダイアログを開く
// - 作品ごとに、元のファイル・透かし入り・編集した動画のどれを投稿するかを選べる
// - 投稿は非同期で、結果は通知(右上のベル)に出る
(function () {
  const dialog = document.getElementById("post-now-dialog");
  if (!dialog) return;
  const $ = (id) => document.getElementById(id);
  const itemsBox = $("pn-items");
  const errorEl = $("pn-error");
  let assets = [];

  function el(tag, props, ...children) {
    const e = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") e.className = v;
      else if (k in e) e[k] = v;
      else e.setAttribute(k, v);
    });
    children.forEach((c) => e.append(c));
    return e;
  }

  function showError(text) {
    errorEl.hidden = !text;
    errorEl.textContent = text || "";
  }

  function row(a) {
    const media = a.kind === "video"
      ? el("video", { src: `/assets/${a.asset_id}/media`, muted: true, preload: "metadata" })
      : el("img", { src: `/assets/${a.asset_id}/media`, alt: "", loading: "lazy" });
    const notes = [];
    notes.push(a.rating ? `区分: ${a.rating}（承認済み）` : "区分: 未承認");
    if (a.fanvue_status === "posted") notes.push("Fanvueに投稿済み");
    if (a.fanvue_status === "failed") notes.push("Fanvueへの前回の投稿は失敗");
    const select = el("select", { class: "pn-version", "aria-label": `${a.name}の投稿するバージョン` });
    a.versions.forEach((v) => select.append(el("option", { value: v.key, textContent: v.label, selected: v.key === a.default })));
    const warn = !a.rating || a.fanvue_status === "posted";
    return el("div", { class: "pn-item", dataset: { assetId: a.asset_id } },
      el("div", { class: "pn-thumb" }, media),
      el("div", { class: "pn-info" },
        el("div", { class: "pn-name", title: a.name, textContent: a.name }),
        el("div", { class: `hint${warn ? " pn-warn" : ""}`, textContent: notes.join(" ／ ") }),
        select));
  }

  window.openPostNow = async function (ids) {
    ids = Array.from(new Set(ids || []));
    if (ids.length === 0) return;
    showError("");
    try {
      assets = await Promise.all(ids.map((id) => fetch(`/api/assets/${id}/versions`).then((r) => {
        if (!r.ok) throw new Error(`作品を取得できませんでした (${r.status})`);
        return r.json();
      })));
    } catch (err) {
      window.showToast(err.message, "error");
      return;
    }
    itemsBox.replaceChildren(...assets.map(row));
    const first = assets.find((a) => a.text) || assets[0];
    $("pn-text").value = first.text || "";
    $("pn-audience").value = first.audience || "subscribers";
    $("pn-price").value = first.price_cents ? (first.price_cents / 100).toString() : "";
    $("pn-submit").disabled = false;
    if (!dialog.open) dialog.showModal();
  };

  $("pn-cancel").addEventListener("click", () => dialog.close());

  $("post-now-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("");
    const items = Array.from(itemsBox.querySelectorAll(".pn-item")).map((r) => ({
      asset_id: r.dataset.assetId,
      version: r.querySelector(".pn-version").value,
    }));
    const audience = $("pn-audience").value;
    const priceText = $("pn-price").value.trim();
    const price_cents = priceText ? Math.round(parseFloat(priceText) * 100) : null;
    const audienceLabel = $("pn-audience").selectedOptions[0].textContent;
    const already = assets.filter((a) => a.fanvue_status === "posted").length;
    const message =
      `${items.length}件を、Fanvueの「${audienceLabel}」に、1つの投稿として投稿します。公開されます。` +
      (price_cents ? `（有料: ${(price_cents / 100).toFixed(2)}ドル）` : "") +
      (already ? `\n※ ${already}件は、Fanvueに投稿済みです（二重の投稿になります）。` : "") +
      "\nよろしいですか？";
    if (!(await window.confirmDialog(message))) return;
    $("pn-submit").disabled = true;
    try {
      const res = await fetch("/api/post-now", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel: "fanvue", items, text: $("pn-text").value, audience, price_cents }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `投稿できませんでした (${res.status})`);
      dialog.close();
      window.showToast("投稿を開始しました。結果は、右上のベルの通知に出ます", "success");
    } catch (err) {
      showError(err.message);
      $("pn-submit").disabled = false;
    }
  });

  // 詳細画面の「操作」メニューから
  const detailButton = $("post-now-open");
  const root = $("asset-root");
  if (detailButton && root) {
    detailButton.addEventListener("click", () => {
      const menu = $("asset-menu");
      if (menu) menu.open = false;
      window.openPostNow([root.dataset.assetId]);
    });
  }
})();
