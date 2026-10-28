// 設定「区分の判定」タブ: 判定し直しを、ジョブキューに積む(保存済みのしきい値で、ワーカーが順に判定する)。
(function () {
  const button = document.getElementById("rejudge-run");
  if (!button) return;
  const result = document.getElementById("rejudge-result");

  async function post(dryRun) {
    const res = await fetch("/api/nsfw/rejudge", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: document.getElementById("rejudge-kind").value, scope: document.getElementById("rejudge-scope").value, dry_run: dryRun }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `実行できませんでした (${res.status})`);
    return data;
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    result.textContent = "";
    try {
      const { matched } = await post(true);
      if (matched === 0) {
        result.textContent = "対象の作品は、ありません。";
        return;
      }
      const kindLabel = document.getElementById("rejudge-kind").selectedOptions[0].textContent;
      const scopeLabel = document.getElementById("rejudge-scope").selectedOptions[0].textContent;
      const ok = await window.confirmDialog(
        `${kindLabel}・${scopeLabel}の、${matched}件の区分の自動判定を、ジョブキューに入れます。保存済みのしきい値で、ワーカーが順に判定します（確定区分は変わりません）。よろしいですか？`
      );
      if (!ok) return;
      const out = await post(false);
      result.textContent = `${out.queued}件を、キューに入れました` + (out.skipped ? `（${out.skipped}件は、すでに待機中・実行中）` : "") + "。進み具合は、一覧のAI処理の表示で見られます。";
      window.showToast(`区分の自動判定の判定し直しを、${out.queued}件、キューに入れました`, "success");
    } catch (err) {
      result.textContent = err.message;
      window.showToast(err.message, "error");
    } finally {
      button.disabled = false;
    }
  });
})();
