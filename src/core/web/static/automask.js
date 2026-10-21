// センシティブ領域の自動検出 → ぼかし・モザイクの加工版。詳細画面のメニューと、一覧の一括操作から開く。
// - window.openAutomask(assetIds, { source, from }) で開く。検出と加工は、ワーカーが行う(終わると通知に出る)
(function () {
  const dialog = document.getElementById("automask-dialog");
  if (!dialog) return;
  const $ = (id) => document.getElementById(id);
  let ids = [];
  let source = "original";
  let loaded = null;

  let lastOpts = null;      // 最後にプレビューした、検出に関わる設定(部位・感度・広げ幅)
  let lastRegions = null;   // そのときに検出できた範囲(種類・強さだけ変えたときは、検出し直さない)
  let previewTimer = null;
  let previewSeq = 0;

  function detectOptions() {
    return { parts: selectedParts(), sensitivity: Number($("am-sensitivity").value), padding: Number($("am-padding").value), interval: Number($("am-interval").value) };
  }
  function styleValue() { return (dialog.querySelector('input[name="am-style"]:checked') || {}).value; }

  // プレビュー: 1件目の作品(動画はサムネイル)で、検出した範囲に、いまの種類・強さをかける。検出できなければ、真ん中あたりの矩形に
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreview, 250);
  }
  async function runPreview() {
    if (!ids.length || !loaded || !loaded.available || selectedParts().length === 0) {
      $("am-preview-wrap").hidden = true;
      return;
    }
    const seq = ++previewSeq;
    const opts = detectOptions();
    const key = JSON.stringify([opts.parts, opts.sensitivity, opts.padding, ids[0], source]);
    const reuse = lastOpts === key && lastRegions && lastRegions.length ? lastRegions : null;
    $("am-preview-wrap").hidden = false;
    $("am-preview-stage").classList.add("loading");
    try {
      const res = await fetch("/api/automask/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_id: ids[0], source, options: opts, style: styleValue(), strength: Number($("am-strength").value), regions: reuse }),
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== previewSeq) return;
      if (!res.ok) throw new Error(data.error || `プレビューできませんでした (${res.status})`);
      $("am-preview").src = data.image;
      lastOpts = key;
      lastRegions = data.detected ? data.regions : null;
      const which = ids.length > 1 ? "選んだ1件目の作品" : "この作品";
      $("am-preview-note").textContent = data.detected
        ? `プレビュー: ${which}で、${data.regions.length}か所を検出しました。`
        : `プレビュー: ${which}では、検出できませんでした。強さの見え方を確かめるため、真ん中あたりの四角にかけています（実際には、検出できなければ、加工版は作りません）。`;
    } catch (err) {
      if (seq !== previewSeq) return;
      $("am-preview-note").textContent = err.message;
    } finally {
      if (seq === previewSeq) $("am-preview-stage").classList.remove("loading");
    }
  }

  function bindValue(inputId, outId) {
    $(inputId).addEventListener("input", () => { $(outId).textContent = $(inputId).value; schedulePreview(); });
  }
  bindValue("am-sensitivity", "am-sensitivity-value");
  bindValue("am-padding", "am-padding-value");
  bindValue("am-strength", "am-strength-value");
  dialog.querySelectorAll('input[name="am-style"]').forEach((r) => r.addEventListener("change", schedulePreview));

  function selectedParts() {
    return Array.from($("am-parts").querySelectorAll("input:checked")).map((i) => i.value);
  }

  function refresh() {
    const ok = loaded && loaded.available && ids.length > 0 && selectedParts().length > 0;
    $("am-run").disabled = !ok;
    $("am-missing").hidden = !loaded || loaded.available;
    $("am-summary").textContent = ids.length ? `選んだ${ids.length}件で、センシティブな領域を探して、隠した加工版を作ります。` : "";
  }

  async function load() {
    if (loaded) return;
    loaded = await (await fetch("/api/automask/options")).json();
    const setRange = (id, outId, v) => { $(id).value = v; $(outId).textContent = v; };
    setRange("am-sensitivity", "am-sensitivity-value", loaded.sensitivity.default);
    setRange("am-padding", "am-padding-value", loaded.padding.default);
    setRange("am-strength", "am-strength-value", loaded.strength.default);
    $("am-interval").value = loaded.interval.default;
    const radio = dialog.querySelector(`input[name="am-style"][value="${loaded.style}"]`);
    if (radio) radio.checked = true;
    $("am-parts").replaceChildren(...loaded.parts.map((p) => {
      const input = Object.assign(document.createElement("input"), { type: "checkbox", value: p.id, checked: p.default });
      input.addEventListener("change", () => { refresh(); schedulePreview(); });
      const label = document.createElement("label");
      label.className = "dup-choice";
      label.append(input, ` ${p.label}`);
      return label;
    }));
  }

  window.openAutomask = async function (assetIds, opts) {
    ids = assetIds;
    source = (opts && opts.source) || "original";
    $("am-error").hidden = true;
    try {
      await load();
    } catch (e) {
      window.showToast("検出の設定を取得できませんでした", "error");
      return;
    }
    refresh();
    lastOpts = lastRegions = null;
    if (!dialog.open) dialog.showModal();
    runPreview();
  };

  $("am-close").addEventListener("click", () => dialog.close());
  $("am-cancel").addEventListener("click", () => dialog.close());

  $("am-run").addEventListener("click", async () => {
    $("am-run").disabled = true;
    $("am-error").hidden = true;
    try {
      const res = await fetch("/api/automask/apply-batch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          asset_ids: ids, source,
          options: detectOptions(),
          style: styleValue(),
          strength: Number($("am-strength").value),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `登録できませんでした (${res.status})`);
      dialog.close();
      const failed = data.failed || [];
      window.showToast(
        `${data.queued}件の検出を登録しました。終わると通知に出ます（見つからない作品は、加工版を作りません）` + (failed.length ? `（${failed.length}件は登録できませんでした: ${failed[0].error}）` : ""),
        failed.length ? "error" : "success"
      );
      if (window.refreshLibrary) window.refreshLibrary(true);
    } catch (err) {
      $("am-error").hidden = false;
      $("am-error").textContent = err.message;
    } finally {
      refresh();
    }
  });

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-automask]");
    if (!opener) return;
    const menu = opener.closest("details");
    if (menu) menu.open = false;
    const assetId = (document.getElementById("mask-dialog") || {}).dataset && document.getElementById("mask-dialog").dataset.assetId;
    if (!assetId) return;
    $("am-interval-wrap").hidden = opener.dataset.media === "image" || (!opener.dataset.media && (document.getElementById("mask-dialog").dataset.kind === "image"));
    window.openAutomask([assetId], { source: opener.dataset.source });
  });
})();
