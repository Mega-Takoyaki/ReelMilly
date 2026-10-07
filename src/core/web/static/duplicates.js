// 重複の確認ダイアログ: アップロードしようとした重複(保留中)と、登録済みの重複グループの扱いを、まとめて決める。
// - window.openDuplicates() で開く(アップロード直後、通知のボタン、一覧のベル)
(function () {
  const dialog = document.getElementById("dup-dialog");
  if (!dialog) return;

  const $ = (id) => document.getElementById(id);
  let data = { pending: [], groups: [] };

  function el(tag, props, ...children) {
    const e = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") e.className = v;
      else if (k === "dataset") Object.assign(e.dataset, v);
      else if (k in e) e[k] = v;
      else e.setAttribute(k, v);
    });
    children.forEach((c) => e.append(c));
    return e;
  }

  function thumb(src, kind, label) {
    const media = kind === "video" ? el("video", { src, muted: true, preload: "metadata" }) : el("img", { src, alt: label, loading: "lazy" });
    return el("div", { class: "dup-thumb" }, media);
  }

  function assetThumb(a, extra) {
    const t = thumb(`/assets/${a.id}/media`, null, a.id);
    const caption = el("div", { class: "dup-cap" },
      el("div", { class: "dup-name", title: a.original_name || a.id }, a.original_name || a.id),
      el("div", { class: "hint" }, `${a.created_at ? a.created_at.slice(0, 10) : ""} ${a.deleted_at ? "（ごみ箱）" : ""}`),
    );
    const link = el("a", { href: `/assets/${a.id}`, target: "_blank", rel: "noopener", class: "dup-link" }, "開く");
    return el("div", { class: "dup-asset" }, t, caption, link, ...(extra ? [extra] : []));
  }

  function radio(name, value, text, checked) {
    const input = el("input", { type: "radio", name, value, checked: !!checked });
    return el("label", { class: "dup-choice" }, input, ` ${text}`);
  }

  // --- 保留中のアップロード ---
  function renderPending() {
    const list = $("dup-pending-list");
    list.replaceChildren(
      ...data.pending.map((p, i) => {
        const neu = el("div", { class: "dup-asset dup-new" },
          thumb(`/api/duplicates/pending/${p.token}/${encodeURIComponent(p.name)}`, null, p.name),
          el("div", { class: "dup-cap" }, el("div", { class: "dup-name", title: p.name }, p.name), el("div", { class: "hint" }, "アップロードしようとしたファイル")));
        const existing = p.existing_assets.length
          ? p.existing_assets.slice(0, 3).map((a) => assetThumb(a))
          : [el("div", { class: "dup-same hint" }, `同じアップロードの中の「${p.same_as}」と、同じ内容です`)];
        const choices = el("div", { class: "dup-choices" },
          radio(`up-${i}`, "skip", "取り込まない（既にあるものを使う）", true),
          radio(`up-${i}`, "import", "別の作品として取り込む"));
        const li = el("li", { class: "dup-item", dataset: { token: p.token, name: p.name } },
          el("div", { class: "dup-compare" }, neu, el("span", { class: "dup-eq" }, "＝"), el("div", { class: "dup-existing" }, ...existing)),
          choices);
        return li;
      })
    );
    $("dup-pending").hidden = data.pending.length === 0;
    $("dup-pending-count").textContent = ` (${data.pending.length}件)`;
  }

  // --- 登録済みの重複グループ ---
  function renderGroups() {
    const list = $("dup-groups-list");
    list.replaceChildren(
      ...data.groups.map((g, i) => {
        const members = g.assets.map((a) => {
          const keep = el("label", { class: "dup-keep" }, el("input", { type: "radio", name: `keep-${i}`, value: a.id, checked: a.keep }), " 残す");
          const badges = [];
          if (a.posted_to.length) badges.push(el("span", { class: "badge status-ready" }, `投稿済み: ${a.posted_to.join("・")}`));
          if (a.content_rating_confirmed) badges.push(el("span", { class: "badge status-ready" }, "承認済み"));
          return assetThumb(a, el("div", {}, keep, ...badges));
        });
        const mode = el("div", { class: "dup-choices" },
          radio(`mode-${i}`, "trash_others", "選んだ1つ以外を、ごみ箱へ移す", true),
          radio(`mode-${i}`, "keep_all", "すべて残す（重複と承知している）"));
        return el("li", { class: "dup-item", dataset: { hash: g.hash } },
          el("div", { class: "dup-members" }, ...members), mode);
      })
    );
    $("dup-groups").hidden = data.groups.length === 0;
    $("dup-groups-count").textContent = ` (${data.groups.length}組)`;
  }

  async function load() {
    const res = await fetch("/api/duplicates");
    data = await res.json();
    renderPending();
    renderGroups();
    $("dup-empty").hidden = data.pending.length + data.groups.length > 0;
    $("dup-run").disabled = data.pending.length + data.groups.length === 0;
  }

  window.openDuplicates = async function () {
    try {
      await load();
    } catch (e) {
      window.showToast("重複の情報を取得できませんでした", "error");
      return;
    }
    if (!dialog.open) dialog.showModal();
  };

  // --- まとめて選ぶ ---
  function setAll(selector, value) {
    dialog.querySelectorAll(selector).forEach((r) => { r.checked = r.value === value; });
  }
  $("dup-pending-skip-all").addEventListener("click", () => setAll("#dup-pending-list input[type=radio]", "skip"));
  $("dup-pending-import-all").addEventListener("click", () => setAll("#dup-pending-list input[type=radio]", "import"));
  $("dup-groups-trash-all").addEventListener("click", () => setAll('#dup-groups-list input[name^="mode-"]', "trash_others"));
  $("dup-groups-keep-all").addEventListener("click", () => setAll('#dup-groups-list input[name^="mode-"]', "keep_all"));

  // --- 実行 ---
  $("dup-run").addEventListener("click", async () => {
    const uploads = Array.from($("dup-pending-list").children).map((li, i) => ({
      token: li.dataset.token,
      name: li.dataset.name,
      action: (li.querySelector(`input[name="up-${i}"]:checked`) || {}).value || "skip",
    }));
    const groups = Array.from($("dup-groups-list").children).map((li, i) => ({
      hash: li.dataset.hash,
      action: (li.querySelector(`input[name="mode-${i}"]:checked`) || {}).value || "keep_all",
      keep_id: (li.querySelector(`input[name="keep-${i}"]:checked`) || {}).value,
    }));
    const trashCount = groups.filter((g) => g.action === "trash_others").length;
    if (trashCount > 0) {
      const ok = await window.confirmDialog(`${trashCount}組の重複について、選んだ1つ以外をごみ箱へ移します（ごみ箱から戻せます）。よろしいですか？`);
      if (!ok) return;
    }
    $("dup-run").disabled = true;
    try {
      const res = await fetch("/api/duplicates/resolve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ uploads, groups }) });
      const out = await res.json();
      if (!res.ok) throw new Error(out.error || res.status);
      const parts = [];
      if (out.uploads) parts.push(`取り込み ${out.uploads.imported}件、破棄 ${out.uploads.skipped}件`);
      if (out.groups) parts.push(`ごみ箱へ ${out.groups.trashed}件、残す ${out.groups.kept_all}組`);
      window.showToast(`重複を処理しました（${parts.join("、")}）`, "success");
      if (out.remaining.pending + out.remaining.groups === 0) {
        dialog.close();
        setTimeout(() => window.location.reload(), 500);
      } else {
        await load();
      }
    } catch (e) {
      window.showToast(`処理できませんでした: ${e.message}`, "error");
      $("dup-run").disabled = false;
    }
  });

  $("dup-later").addEventListener("click", () => {
    dialog.close();
    if (document.getElementById("dropzone")) setTimeout(() => window.location.reload(), 200);
  });
  $("dup-close").addEventListener("click", () => $("dup-later").click());
})();
