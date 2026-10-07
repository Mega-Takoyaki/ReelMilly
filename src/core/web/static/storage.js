// 設定画面「ストレージ」タブ: 画像・動画の置き場所の確認・変更(ドライブ/フォルダの選択、コピーして移す、進捗表示)。
(function () {
  const root = document.getElementById("sec-storage");
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const fmtBytes = (n) => {
    if (n == null) return "不明";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
  };

  async function getJson(url) {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);
    return data;
  }

  // --- 現在の状態 ---
  let pollTimer = null;

  async function refresh() {
    let s;
    try {
      s = await getJson("/api/storage/status?usage=1");
    } catch (e) {
      $("storage-now").textContent = "状態を取得できませんでした";
      return;
    }
    $("storage-path").textContent = s.root;
    const badge = $("storage-badge");
    badge.textContent = s.available ? "接続中" : s.code === "migrating" ? "移行中" : "見つかりません";
    badge.className = `job-chip ${s.available ? "worker-on" : "worker-off"}`;
    $("storage-reason").textContent = s.available ? "" : s.reason;
    const parts = [];
    if (s.free_bytes != null) parts.push(`空き ${fmtBytes(s.free_bytes)} / 全体 ${fmtBytes(s.total_bytes)}`);
    if (s.usage) parts.push(`作品 ${s.usage.found}/${s.usage.assets}件のファイル（${fmtBytes(s.usage.bytes)}）`);
    else if (s.asset_files) parts.push(`作品 ${s.asset_files_found}/${s.asset_files}件のファイルを確認`);
    $("storage-detail").textContent = parts.join("　／　");
    $("storage-default").dataset.path = s.default_root;
    renderJob(s.job);
  }

  function renderJob(job) {
    const box = $("storage-job");
    if (!job || job.state === undefined || (job.state !== "running" && !box.dataset.show)) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const bar = box.querySelector(".job-bar span");
    const pct = job.total_bytes ? Math.round((job.done_bytes / job.total_bytes) * 100) : (job.state === "done" ? 100 : 0);
    bar.style.width = `${pct}%`;
    const text = job.state === "running"
      ? `${job.message || "実行中"}　${fmtBytes(job.done_bytes)} / ${fmtBytes(job.total_bytes)}（${pct}%）`
      : job.state === "done" ? `完了しました: ${job.result ? job.result.root : ""}` : `失敗しました: ${job.message}`;
    $("storage-job-text").textContent = text;
    if (job.state === "running") {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(refresh, 1500);
    } else {
      $("storage-apply").disabled = false;
      if (job.state === "done" && box.dataset.show) {
        window.showToast("ストレージの場所を変更しました", "success");
        delete box.dataset.show;
      } else if (job.state === "failed" && box.dataset.show) {
        window.showToast(`ストレージの変更に失敗しました: ${job.message}`, "error");
        delete box.dataset.show;
      }
    }
  }

  // --- ドライブの一覧 ---
  async function loadDrives() {
    const box = $("storage-drives");
    try {
      const { drives } = await getJson("/api/storage/drives");
      box.replaceChildren(
        ...drives.map((d) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "btn-ghost-inline drive-btn";
          b.disabled = d.ready === false;
          const free = d.free_bytes != null ? ` 空き${fmtBytes(d.free_bytes)}` : "";
          b.textContent = `${d.path}${d.label ? " " + d.label : ""}（${d.type}${d.removable ? "・取り外し可" : ""}${free}）`;
          b.addEventListener("click", () => openBrowser(d.path));
          return b;
        })
      );
    } catch (e) {
      box.textContent = "ドライブの一覧を取得できませんでした";
    }
  }

  // --- フォルダの選択(ダイアログ) ---
  const dlg = $("storage-browser");
  let browsePath = "";

  async function openBrowser(path) {
    try {
      const data = await getJson(`/api/storage/browse?path=${encodeURIComponent(path || $("storage-new").value || "C:\\")}`);
      browsePath = data.path;
      $("storage-browse-path").textContent = data.path;
      $("storage-browse-up").disabled = !data.parent;
      $("storage-browse-up").dataset.path = data.parent || "";
      $("storage-browse-list").replaceChildren(
        ...data.folders.map((name) => {
          const li = document.createElement("li");
          const b = document.createElement("button");
          b.type = "button";
          b.className = "folder-item";
          b.textContent = `📁 ${name}`;
          b.addEventListener("click", () => openBrowser(data.path.replace(/[\\/]+$/, "") + "\\" + name));
          li.appendChild(b);
          return li;
        })
      );
      if (data.folders.length === 0) $("storage-browse-list").innerHTML = '<li class="hint">この中にフォルダはありません</li>';
      if (!dlg.open) dlg.showModal();
    } catch (e) {
      window.showToast(`フォルダを開けませんでした: ${e.message}`, "error");
    }
  }

  $("storage-browse-btn").addEventListener("click", () => openBrowser(""));
  $("storage-browse-up").addEventListener("click", (e) => { if (e.currentTarget.dataset.path) openBrowser(e.currentTarget.dataset.path); });
  $("storage-browse-new").addEventListener("click", async () => {
    const name = window.prompt("作るフォルダの名前（このフォルダの中に作ります）", "Reelmilly");
    if (name) {
      browsePath = browsePath.replace(/[\\/]+$/, "") + "\\" + name.trim();
      $("storage-new").value = browsePath;
      dlg.close();
    }
  });
  $("storage-browse-pick").addEventListener("click", () => { $("storage-new").value = browsePath; dlg.close(); });
  $("storage-browse-cancel").addEventListener("click", () => dlg.close());

  $("storage-default").addEventListener("click", (e) => {
    $("storage-new").value = e.currentTarget.dataset.path || "";
  });

  // 方法に応じて、「元のファイルを削除」の表示を切り替える
  function syncMode() {
    const move = root.querySelector('input[name="storage-mode"]:checked').value === "move";
    $("storage-delete-row").hidden = !move;
  }
  root.querySelectorAll('input[name="storage-mode"]').forEach((r) => r.addEventListener("change", syncMode));
  syncMode();

  // --- 変更の実行 ---
  $("storage-apply").addEventListener("click", async () => {
    const path = $("storage-new").value.trim();
    const mode = root.querySelector('input[name="storage-mode"]:checked').value;
    const del = mode === "move" && $("storage-delete").checked;
    if (!path) { $("storage-new").focus(); return; }
    const message = mode === "move"
      ? `いまのライブラリを「${path}」へコピーして、保存先を切り替えます。${del ? "コピーが終わったら、元のファイルを削除します（元に戻せません）。" : "元のファイルは残します。"}大きい場合は時間がかかります。よろしいですか？`
      : `保存先を「${path}」に切り替えます（ファイルはコピーしません）。この場所に、作品のファイルが入っている必要があります。よろしいですか？`;
    if (!(await window.confirmDialog(message))) return;
    $("storage-apply").disabled = true;
    $("storage-job").dataset.show = "1";
    try {
      const res = await fetch("/api/storage/change", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, mode, delete_source: del }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);
      window.showToast("ストレージの変更を開始しました", "success");
      refresh();
    } catch (e) {
      $("storage-apply").disabled = false;
      delete $("storage-job").dataset.show;
      window.showToast(`変更できませんでした: ${e.message}`, "error");
    }
  });

  refresh();
  loadDrives();
})();
