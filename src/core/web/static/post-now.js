// 今すぐ投稿: 一覧(選択した作品)・詳細画面(その作品)から、作品とバージョンを選んで、いますぐ1つの投稿として投稿する。
// - window.openPostNow(assetIds) でダイアログを開く
// - 投稿先: Fanvue / X(連携済みのときだけ選べる)
// - 作品ごとに、元のファイル・透かし入り・編集した動画のどれを投稿するかを選べる
// - Xは、画像4枚まで/動画1本・文字数(全角は2)・センシティブ指定(sfw以外は必須。explicitは投稿しない)を、投稿前に検査する
// - 投稿は非同期で、結果は通知(右上のベル)に出る
(function () {
  const dialog = document.getElementById("post-now-dialog");
  if (!dialog) return;
  const $ = (id) => document.getElementById(id);
  const itemsBox = $("pn-items");
  const errorEl = $("pn-error");
  const X_MAX_WEIGHT = 280;
  const X_MAX_IMAGES = 4;
  let assets = [];
  let beforeGenerate = null;

  function el(tag, props, ...children) {
    const e = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") e.className = v;
      else if (k === "dataset") Object.assign(e.dataset, v);  // datasetは読み取り専用の項目なので、代入ではなく中身を移す(これが無く、作品IDが空で送られていた)
      else if (k in e) e[k] = v;
      else e.setAttribute(k, v);
    });
    children.forEach((c) => e.append(c));
    return e;
  }

  const currentChannel = () => (dialog.querySelector('input[name="pn-channel"]:checked') || {}).value || "fanvue";
  const xWeight = (text) => Array.from(text).reduce((n, ch) => n + (ch.codePointAt(0) < 0x2e80 ? 1 : 2), 0);

  function showError(text) {
    errorEl.hidden = !text;
    errorEl.textContent = text || "";
  }

  function postedStatus(a) {
    return currentChannel() === "x" ? a.x_status : a.fanvue_status;
  }

  function row(a) {
    const media = a.kind === "video"
      ? el("video", { src: `/assets/${a.asset_id}/media`, muted: true, preload: "metadata" })
      : el("img", { src: `/assets/${a.asset_id}/media`, alt: "", loading: "lazy" });
    const select = el("select", { class: "pn-version", "aria-label": `${a.name}の投稿するバージョン` });
    a.versions.forEach((v) => select.append(el("option", { value: v.key, textContent: v.label, selected: v.key === a.default, dataset: { mediaType: v.media_type } })));
    select.addEventListener("change", applyChannel);
    return el("div", { class: "pn-item", dataset: { assetId: a.asset_id } },
      el("div", { class: "pn-thumb" }, media),
      el("div", { class: "pn-info" },
        el("div", { class: "pn-name", title: a.name, textContent: a.name }),
        el("div", { class: "hint pn-note" }),
        select));
  }

  // 選んでいる投稿先に合わせて、表示・注意書き・検査を更新する
  function applyChannel() {
    const channel = currentChannel();
    const isX = channel === "x";
    $("pn-fanvueonly").hidden = isX;
    $("pn-xonly").hidden = !isX;
    const label = isX ? "X" : "Fanvue";
    itemsBox.querySelectorAll(".pn-item").forEach((r) => {
      const a = assets.find((x) => x.asset_id === r.dataset.assetId);
      const notes = [a.rating ? `区分: ${a.rating}（承認済み）` : "区分: 未承認"];
      const status = postedStatus(a);
      if (status === "posted") notes.push(`${label}に投稿済み`);
      if (status === "failed") notes.push(`${label}への前回の投稿は失敗`);
      if (a.status && a.status !== "ready") notes.push(`状態: ${a.status}（投稿すると、readyにします）`);
      const note = r.querySelector(".pn-note");
      note.textContent = notes.join(" ／ ");
      note.classList.toggle("pn-warn", !a.rating || status === "posted");
    });

    let problem = "";
    if (isX) {
      const types = Array.from(itemsBox.querySelectorAll(".pn-version")).map((s) => s.selectedOptions[0].dataset.mediaType);
      const videos = types.filter((t) => t === "video").length;
      if (videos > 0 && !(videos === 1 && types.length === 1)) problem = "Xでは、動画は1本だけで、画像とは一緒に投稿できません";
      else if (videos === 0 && types.length > X_MAX_IMAGES) problem = `Xでは、1つの投稿に付けられる画像は${X_MAX_IMAGES}枚までです`;
      const ratings = assets.map((a) => a.rating);
      if (ratings.includes("explicit")) problem = problem || "区分がexplicit(成人向け)の作品は、Xには投稿しません";
      // センシティブ指定は、最後は人が決める(外せる)。sfwと承認されていない作品は、開いたときに、既定でオンにするだけ
      $("pn-sensitive-note").hidden = !ratings.some((r) => r !== "sfw");
      const weight = xWeight($("pn-text").value);
      $("pn-count").textContent = `Xの文字数: ${weight} / ${X_MAX_WEIGHT}（全角は2文字）`;
      $("pn-count").classList.toggle("pn-warn", weight > X_MAX_WEIGHT);
      if (!problem && weight > X_MAX_WEIGHT) problem = `Xの文字数の上限を超えています（${weight} / ${X_MAX_WEIGHT}）`;
    }
    showError(problem);
    $("pn-submit").disabled = !!problem;
  }

  window.openPostNow = async function (ids) {
    ids = Array.from(new Set(ids || []));
    if (ids.length === 0) return;
    showError("");
    try {
      const [loaded, targets] = await Promise.all([
        Promise.all(ids.map((id) => fetch(`/api/assets/${id}/versions`).then((r) => {
          if (!r.ok) throw new Error(`作品を取得できませんでした (${r.status})`);
          return r.json();
        }))),
        fetch("/api/post-targets").then((r) => r.json()).catch(() => ({})),
      ]);
      assets = loaded;
      const xRadio = dialog.querySelector('input[name="pn-channel"][value="x"]');
      xRadio.disabled = !targets.x;
      $("pn-x-note").textContent = targets.x ? "" : " （未連携。設定の「X」タブで連携）";
      dialog.querySelector('input[name="pn-channel"][value="fanvue"]').checked = true;
      dialog.querySelector('input[name="pn-channel"][value="fanvue"]').disabled = targets.fanvue === false;
    } catch (err) {
      window.showToast(err.message, "error");
      return;
    }
    itemsBox.replaceChildren(...assets.map(row));
    const first = assets.find((a) => a.text) || assets[0];
    $("pn-text").value = first.text || "";
    $("pn-audience").value = first.audience || "subscribers";
    $("pn-price").value = first.price_cents ? (first.price_cents / 100).toString() : "";
    $("pn-sensitive").checked = assets.some((a) => a.rating !== "sfw");  // 既定: sfwと承認されていない作品は、オン(人が外せる)
    $("pn-ai").checked = true;
    beforeGenerate = null;
    $("pn-undo").hidden = true;
    applyChannel();
    if (!dialog.open) dialog.showModal();
  };

  $("pn-cancel").addEventListener("click", () => dialog.close());
  dialog.querySelectorAll('input[name="pn-channel"]').forEach((r) => r.addEventListener("change", applyChannel));
  $("pn-text").addEventListener("input", applyChannel);

  // 投稿文の生成(設定のプロンプトとAIで、都度つくる。投稿先に合わせて、FanvueまたはX用)。生成前の文は、「戻す」で復元できる
  $("pn-generate").addEventListener("click", async () => {
    const button = $("pn-generate");
    showError("");
    button.disabled = true;
    button.classList.add("is-busy");
    try {
      const res = await fetch("/api/captions/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: assets.map((a) => a.asset_id), channel: currentChannel() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `生成できませんでした (${res.status})`);
      beforeGenerate = $("pn-text").value;
      $("pn-text").value = data.text;
      $("pn-undo").hidden = !beforeGenerate;
      applyChannel();
      window.showToast(data.attempts > 1 ? `投稿文を生成しました（${data.attempts}回目で形式に合いました）` : "投稿文を生成しました", "success");
    } catch (err) {
      showError(err.message);
    } finally {
      button.disabled = false;
      button.classList.remove("is-busy");
    }
  });
  $("pn-undo").addEventListener("click", () => {
    if (beforeGenerate !== null) $("pn-text").value = beforeGenerate;
    $("pn-undo").hidden = true;
    applyChannel();
  });

  $("post-now-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("");
    const channel = currentChannel();
    const items = Array.from(itemsBox.querySelectorAll(".pn-item")).map((r) => ({
      asset_id: r.dataset.assetId,
      version: r.querySelector(".pn-version").value,
    }));
    const already = assets.filter((a) => postedStatus(a) === "posted").length;
    let body;
    let message;
    if (channel === "x") {
      const sensitive = $("pn-sensitive").checked;
      body = { channel, items, text: $("pn-text").value, sensitive, made_with_ai: $("pn-ai").checked };
      message = `${items.length}件を、Xに、1つの投稿として投稿します。公開されます。` +
        (sensitive ? "（センシティブ指定あり）" : "") +
        "\n※ X APIは従量課金のため、投稿・アップロードに料金がかかります。" +
        (already ? `\n※ ${already}件は、Xに投稿済みです（二重の投稿になります）。` : "") +
        "\nよろしいですか？";
    } else {
      const audience = $("pn-audience").value;
      const priceText = $("pn-price").value.trim();
      const price_cents = priceText ? Math.round(parseFloat(priceText) * 100) : null;
      body = { channel, items, text: $("pn-text").value, audience, price_cents };
      message = `${items.length}件を、Fanvueの「${$("pn-audience").selectedOptions[0].textContent}」に、1つの投稿として投稿します。公開されます。` +
        (price_cents ? `（有料: ${(price_cents / 100).toFixed(2)}ドル）` : "") +
        (already ? `\n※ ${already}件は、Fanvueに投稿済みです（二重の投稿になります）。` : "") +
        "\nよろしいですか？";
    }
    if (!(await window.confirmDialog(message))) return;
    $("pn-submit").disabled = true;
    try {
      const res = await fetch("/api/post-now", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
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
