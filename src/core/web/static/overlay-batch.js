// 一覧で選んだ複数の作品へ、保存したテロップ・スタンプのテンプレートを、一括で適用する。
(function () {
  const dialog = document.getElementById("overlay-batch-dialog");
  if (!dialog) return;
  const $ = (id) => document.getElementById(id);
  let ids = [];
  let templates = [];

  function refresh() {
    const t = templates.find((x) => x.id === $("ob-template").value);
    $("ob-run").disabled = !t || ids.length === 0;
    $("ob-summary").textContent = t ? `選んだ${ids.length}件に、「${t.name}」（${t.layers.length}個）を重ねた加工版を作ります。` : "";
  }
  $("ob-template").addEventListener("change", refresh);
  $("ob-close").addEventListener("click", () => dialog.close());
  $("ob-cancel").addEventListener("click", () => dialog.close());

  window.openOverlayBatch = async function (assetIds) {
    ids = assetIds;
    $("ob-error").hidden = true;
    const options = await (await fetch("/api/overlay/options")).json();
    templates = options.templates;
    $("ob-template").replaceChildren(...templates.map((t) => new Option(`${t.name}（${t.layers.length}個）`, t.id)));
    $("ob-empty").hidden = templates.length > 0;
    $("ob-template").hidden = templates.length === 0;
    refresh();
    if (!dialog.open) dialog.showModal();
  };

  $("ob-run").addEventListener("click", async () => {
    $("ob-run").disabled = true;
    try {
      const res = await fetch("/api/overlay/apply-batch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: ids, template_id: $("ob-template").value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `登録できませんでした (${res.status})`);
      dialog.close();
      const failed = data.failed || [];
      window.showToast(
        `${data.queued}件の作成を登録しました。終わると通知に出ます` + (failed.length ? `（${failed.length}件は登録できませんでした: ${failed[0].error}）` : ""),
        failed.length ? "error" : "success"
      );
    } catch (err) {
      $("ob-error").hidden = false;
      $("ob-error").textContent = err.message;
    } finally {
      refresh();
    }
  });
})();
