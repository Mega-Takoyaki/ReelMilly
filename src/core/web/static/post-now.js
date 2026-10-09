// 今すぐ投稿: 一覧(選択した作品)・詳細画面(その作品)から、作品とバージョンを選んで、いますぐ1つの投稿として投稿する。
// - window.openPostNow(assetIds) でダイアログを開く
// - 投稿先: Fanvue / X(連携済みのときだけ選べる)
// - 作品ごとに、元のファイル・透かし入り・編集した動画のどれを投稿するかを選べる
// - Xは、画像4枚まで/動画1本・文字数(全角は2)を、投稿前に検査する(Xの仕様による制限だけ。区分による制限は設けない。センシティブ指定は、人が決める)
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
  let sensitiveByUser = false;  // 利用者が、センシティブ指定を、自分で切り替えたか(切り替えたあとは、勝手に変えない)

  // 選んでいるファイルの区分(加工版は、承認するまで、原本から引き継ぐ)。sfwと承認されていないものがあれば、既定でオン
  function selectedRatings() {
    return Array.from(itemsBox.querySelectorAll(".pn-version")).map((s) => s.selectedOptions[0].dataset.rating || null);
  }
  function setSensitiveDefault() {
    $("pn-sensitive").checked = selectedRatings().some((r) => r !== "sfw");
  }

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

  function row(a, preselect) {
    const media = a.kind === "video"
      ? el("video", { src: `/assets/${a.asset_id}/media`, muted: true, preload: "metadata" })
      : el("img", { src: `/assets/${a.asset_id}/media`, alt: "", loading: "lazy" });
    const select = el("select", { class: "pn-version", "aria-label": `${a.name}の投稿するバージョン` });
    const chosen = (preselect && preselect[a.asset_id]) || a.default;
    a.versions.forEach((v) => select.append(el("option", {
      value: v.key, textContent: v.label, selected: v.key === chosen,
      dataset: { mediaType: v.media_type, rating: v.rating || "", source: v.rating_source || "none" },
    })));
    select.addEventListener("change", () => { sensitiveByUser ? null : setSensitiveDefault(); applyChannel(); });
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
    // この作品を、すでに予約している場合は、知らせる(二重の予約を避ける)
    const reserved = assets.flatMap((a) => (a.scheduled || []).filter((s) => s.channel === channel).map((s) => `${a.name} → ${s.run_at_label}`));
    $("pn-scheduled-note").hidden = reserved.length === 0;
    $("pn-scheduled-note").textContent = reserved.length ? `${label}へ、すでに予約されています: ${reserved.join(" ／ ")}` : "";
    itemsBox.querySelectorAll(".pn-item").forEach((r) => {
      const a = assets.find((x) => x.asset_id === r.dataset.assetId);
      const picked = r.querySelector(".pn-version").selectedOptions[0].dataset;
      const SOURCE = { own: "承認済み", inherited: "原本から引き継ぎ", none: "" };
      const notes = [picked.rating ? `区分: ${picked.rating}（${SOURCE[picked.source] || "承認済み"}）` : "区分: 未承認"];
      const status = postedStatus(a);
      if (status === "posted") notes.push(`${label}に投稿済み`);
      if (status === "failed") notes.push(`${label}への前回の投稿は失敗`);
      if (a.status && a.status !== "ready") notes.push(`状態: ${a.status}（投稿すると、readyにします）`);
      const note = r.querySelector(".pn-note");
      note.textContent = notes.join(" ／ ");
      note.classList.toggle("pn-warn", !picked.rating || status === "posted");
    });

    let problem = "";
    if (isX) {
      const types = Array.from(itemsBox.querySelectorAll(".pn-version")).map((s) => s.selectedOptions[0].dataset.mediaType);
      const videos = types.filter((t) => t === "video").length;
      if (videos > 0 && !(videos === 1 && types.length === 1)) problem = "Xでは、動画は1本だけで、画像とは一緒に投稿できません";
      else if (videos === 0 && types.length > X_MAX_IMAGES) problem = `Xでは、1つの投稿に付けられる画像は${X_MAX_IMAGES}枚までです`;
      const ratings = selectedRatings();
      // センシティブ指定は、最後は人が決める(外せる)。sfwと承認されていない作品は、開いたときに、既定でオンにするだけ
      $("pn-sensitive-note").hidden = !ratings.some((r) => r !== "sfw");
      const weight = xWeight($("pn-text").value);
      $("pn-count").hidden = false;
      $("pn-count").textContent = `Xの文字数: ${weight} / ${X_MAX_WEIGHT}（全角は2文字）`;
      $("pn-count").classList.toggle("pn-warn", weight > X_MAX_WEIGHT);
      if (!problem && weight > X_MAX_WEIGHT) problem = `Xの文字数の上限を超えています（${weight} / ${X_MAX_WEIGHT}）`;
    }
    if (!isX) $("pn-count").hidden = true;
    showError(problem);
    $("pn-submit").disabled = !!problem;
    updateSummary();
  }

  window.openPostNow = async function (ids, preselect) {
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
      $("pn-x-note").textContent = targets.x ? "" : "Xは、未連携です（設定の「X」タブで連携すると、選べます）";
      dialog.querySelector('input[name="pn-channel"][value="fanvue"]').checked = true;
      dialog.querySelector('input[name="pn-channel"][value="fanvue"]').disabled = targets.fanvue === false;
    } catch (err) {
      window.showToast(err.message, "error");
      return;
    }
    itemsBox.replaceChildren(...assets.map((a) => row(a, preselect)));
    const first = assets.find((a) => a.text) || assets[0];
    $("pn-text").value = first.text || "";
    $("pn-audience").value = first.audience || "subscribers";
    $("pn-price").value = first.price_cents ? (first.price_cents / 100).toString() : "";
    sensitiveByUser = false;
    setSensitiveDefault();  // 既定: sfwと承認されていないファイルを含めば、オン(人が外せる)
    $("pn-ai").checked = true;
    beforeGenerate = null;
    $("pn-undo").hidden = true;
    dialog.querySelector('input[name="pn-when"][value="now"]').checked = true;
    applyWhen();
    applyChannel();
    if (!dialog.open) dialog.showModal();
  };

  $("pn-cancel").addEventListener("click", () => dialog.close());
  $("pn-close").addEventListener("click", () => dialog.close());

  // 下部の要約(「Fanvue ・ 購読者のみ ・ 2件 ・ 今すぐ」)。何を押すと、何が起きるかを、ボタンのそばに出す
  function updateSummary() {
    const isX = currentChannel() === "x";
    const parts = [isX ? "X" : "Fanvue"];
    if (!isX) parts.push($("pn-audience").selectedOptions[0].textContent);
    parts.push(`${assets.length}件を、1つの投稿に`);
    parts.push(isLater() ? "予約" : "今すぐ");
    $("pn-summary").textContent = parts.join(" ・ ");
  }
  $("pn-audience").addEventListener("change", updateSummary);

  // 投稿のタイミング(今すぐ/予約)。予約を選ぶと、日時の欄を出し、ボタンを「予約する」にする
  const isLater = () => (dialog.querySelector('input[name="pn-when"]:checked') || {}).value === "later";
  function localInputValue(date) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
  function applyWhen() {
    const later = isLater();
    $("pn-when-detail").hidden = !later;
    $("pn-submit").textContent = later ? "予約する" : "今すぐ投稿する";
    updateSummary();
    if (later && !$("pn-run-at").value) {
      const soon = new Date(Date.now() + 60 * 60 * 1000);   // 既定: 1時間後(5分単位)
      soon.setMinutes(Math.ceil(soon.getMinutes() / 5) * 5, 0, 0);
      $("pn-run-at").value = localInputValue(soon);
    }
    if (later) $("pn-run-at").min = localInputValue(new Date());
  }
  dialog.querySelectorAll('input[name="pn-when"]').forEach((r) => r.addEventListener("change", applyWhen));
  dialog.querySelectorAll('input[name="pn-channel"]').forEach((r) => r.addEventListener("change", applyChannel));
  $("pn-text").addEventListener("input", applyChannel);
  $("pn-sensitive").addEventListener("change", () => { sensitiveByUser = true; });

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
    let url = "/api/post-now";
    let doneMessage = "投稿を開始しました。結果は、右上のベルの通知に出ます";
    if (isLater()) {
      const value = $("pn-run-at").value;
      if (!value || Number.isNaN(new Date(value).getTime())) { showError("予約する日時を入れてください"); return; }
      const when = new Date(value);
      if (when.getTime() <= Date.now()) { showError("予約の日時は、これから先の日時にしてください"); return; }
      body.run_at = when.toISOString();
      url = "/api/scheduled-posts";
      message = message.replace("に、1つの投稿として投稿します。公開されます。", `に、${when.getMonth() + 1}/${when.getDate()} ${String(when.getHours()).padStart(2, "0")}:${String(when.getMinutes()).padStart(2, "0")} に、1つの投稿として予約します。時間になると、自動で公開されます（reelmilly watch が動いている間）。`);
      doneMessage = "予約しました。設定の「投稿スケジュール」タブで、確認・取り消しができます";
    }
    if (!(await window.confirmDialog(message))) return;
    $("pn-submit").disabled = true;
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `投稿できませんでした (${res.status})`);
      dialog.close();
      window.showToast(doneMessage, "success");
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
