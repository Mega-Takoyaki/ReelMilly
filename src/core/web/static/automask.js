// センシティブ領域の自動検出 → ぼかし・モザイクの加工版。詳細画面のメニューと、一覧の一括操作から開く。
// - window.openAutomask(assetIds, { source, from }) で開く。検出と加工は、ワーカーが行う(終わると通知に出る)
(function () {
  const dialog = document.getElementById("automask-dialog");
  if (!dialog) return;
  const $ = (id) => document.getElementById(id);
  let ids = [];
  let source = "original";
  let loaded = null;

  function bindValue(inputId, outId) {
    $(inputId).addEventListener("input", () => { $(outId).textContent = $(inputId).value; });
  }
  bindValue("am-sensitivity", "am-sensitivity-value");
  bindValue("am-padding", "am-padding-value");
  bindValue("am-strength", "am-strength-value");

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
    $("am-parts").replaceChildren(...loaded.parts.map((p) => {
      const input = Object.assign(document.createElement("input"), { type: "checkbox", value: p.id, checked: p.default });
      input.addEventListener("change", refresh);
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
    if (!dialog.open) dialog.showModal();
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
          options: { parts: selectedParts(), sensitivity: Number($("am-sensitivity").value), padding: Number($("am-padding").value), interval: Number($("am-interval").value) },
          style: (dialog.querySelector('input[name="am-style"]:checked') || {}).value,
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
