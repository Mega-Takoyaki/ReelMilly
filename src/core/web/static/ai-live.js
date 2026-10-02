// AI処理(sfw/nsfw判定・説明文生成)の非同期実行を扱う。一覧・詳細画面共通。
// - 処理の開始(キューへ追加)
// - 定期ポーリングで進捗を取得し、完了したものから順に画面表示を更新する
// - 完了・失敗をトースト通知する
(function () {
  const LABEL = { nsfw: "sfw/nsfw判定", describe: "説明文生成・タグ付与" };
  const BUSY_LABEL = { nsfw: "判定", describe: "説明生成" };
  const POLL_MS = 3000;

  const banner = document.getElementById("ai-status");
  const cards = Array.from(document.querySelectorAll("[data-asset-id].asset-card, #asset-root[data-asset-id]"));
  const ids = Array.from(new Set(cards.map((el) => el.dataset.assetId)));

  let prev = null; // 直前のポーリング結果(assets)
  let prevBusy = false;
  let doneSinceBusy = 0;
  let timer = null;
  let workerAlive = true;

  function short(id) {
    return id.length > 12 ? id.slice(-12) : id;
  }

  function isActive(task) {
    return task && (task.status === "queued" || task.status === "running");
  }

  // --- 表示更新 -------------------------------------------------------------

  function badge(cls, text) {
    const span = document.createElement("span");
    span.className = `badge ${cls}`;
    span.textContent = text;
    return span;
  }

  function renderCardBadges(card, a) {
    const meta = card.querySelector(".asset-meta");
    if (!meta) return;
    meta.innerHTML = "";
    meta.appendChild(badge(`status-${a.status}`, a.status));
    if (a.content_rating) {
      meta.appendChild(badge(`rating-${a.content_rating}`, a.content_rating));
    } else if (a.nsfw_auto_rating) {
      meta.appendChild(badge("rating-auto", `自動判定: ${a.nsfw_auto_rating}（未承認）`));
    } else {
      meta.appendChild(badge("rating-none", "未判定"));
    }
    Object.keys(a.tasks || {}).forEach((kind) => {
      const t = a.tasks[kind];
      if (t.status === "running") meta.appendChild(badge("badge-busy", `${BUSY_LABEL[kind]}中…`));
      else if (t.status === "queued") meta.appendChild(badge("badge-busy", `${BUSY_LABEL[kind]}待機中`));
    });
  }

  function renderDetailTaskState(a) {
    const el = document.getElementById("ai-task-state");
    if (!el) return;
    const words = { queued: "待機中", running: "実行中…", done: "完了", failed: "失敗" };
    const parts = Object.keys(LABEL).map((kind) => {
      const t = (a.tasks || {})[kind];
      return t ? `${LABEL[kind]}: ${words[t.status]}${t.error ? "（" + t.error + "）" : ""}` : null;
    });
    el.textContent = parts.filter(Boolean).join(" / ");
    document.querySelectorAll("[data-ai-run]").forEach((btn) => {
      const kind = btn.dataset.aiRun;
      const kinds = kind === "both" ? ["nsfw", "describe"] : [kind];
      btn.disabled = kinds.some((k) => isActive((a.tasks || {})[k]));
    });
  }

  async function refreshDetailRegions(a) {
    // 結果(判定・説明・ステータス)が変わった詳細画面の表示を、ページ全体の再読み込みなしで差し替える
    try {
      const res = await fetch(window.location.href, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      ["live-props", "live-status"].forEach((id) => {
        const fresh = doc.getElementById(id);
        const cur = document.getElementById(id);
        if (fresh && cur) cur.innerHTML = fresh.innerHTML;
      });
      if (window.renderTagList && document.getElementById("tag-list")) window.renderTagList(a.tags);
    } catch (e) {
      /* 次回のポーリングで再試行される */
    }
  }

  function renderBanner(status) {
    if (!banner) return;
    const active = status.queued + status.running;
    if (active === 0) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    const parts = [`AI処理: 実行中 ${status.running}件 / 待機 ${status.queued}件`];
    if (status.worker_alive) {
      if (status.current) parts.push(`処理中: ${short(status.current.asset_id)}（${LABEL[status.current.kind]}）`);
    } else {
      parts.push("ワーカー停止中（別のターミナルで reelmilly watch を起動すると処理が進みます）");
    }
    banner.textContent = parts.join(" / ");
  }

  // --- 完了検知・通知 -------------------------------------------------------

  function detectTransitions(assets) {
    const completed = { nsfw: [], describe: [] };
    const failed = { nsfw: [], describe: [] };
    const changed = [];
    Object.keys(assets).forEach((id) => {
      const cur = assets[id];
      const old = prev && prev[id];
      if (!old) return;
      Object.keys(LABEL).forEach((kind) => {
        const before = old.tasks[kind];
        const now = cur.tasks[kind];
        if (isActive(before) && now && now.status === "done") completed[kind].push(id);
        if (isActive(before) && now && now.status === "failed") failed[kind].push({ id, error: now.error });
      });
      if (old.rev !== cur.rev) changed.push(id);
    });
    return { completed, failed, changed };
  }

  function notify(assets, t) {
    Object.keys(LABEL).forEach((kind) => {
      const list = t.completed[kind];
      if (list.length === 0) return;
      doneSinceBusy += list.length;
      if (list.length === 1) {
        const a = assets[list[0]];
        let detail = "";
        if (kind === "nsfw" && a.nsfw_auto_rating) {
          detail = `: ${a.nsfw_auto_rating}（確信度 ${Number(a.nsfw_auto_confidence).toFixed(2)}）`;
        }
        window.showToast(`${short(list[0])} の${LABEL[kind]}が完了しました${detail}`, "success");
      } else {
        window.showToast(`${LABEL[kind]}が${list.length}件完了しました`, "success");
      }
    });
    Object.keys(LABEL).forEach((kind) => {
      const list = t.failed[kind];
      if (list.length === 0) return;
      const first = list[0];
      window.showToast(
        `${LABEL[kind]}に失敗しました（${list.length}件）: ${short(first.id)} ${first.error || ""}`,
        "error"
      );
    });
  }

  async function poll() {
    try {
      const res = await fetch(`/api/ai-live?ids=${encodeURIComponent(ids.join(","))}`);
      if (!res.ok) return;
      const data = await res.json();
      workerAlive = data.status.worker_alive;
      const t = detectTransitions(data.assets);
      notify(data.assets, t);

      cards.forEach((el) => {
        const a = data.assets[el.dataset.assetId];
        if (!a) return;
        if (el.classList.contains("asset-card")) renderCardBadges(el, a);
        else renderDetailTaskState(a);
      });
      const detailRoot = document.getElementById("asset-root");
      if (detailRoot) {
        const a = data.assets[detailRoot.dataset.assetId];
        if (a && t.changed.includes(detailRoot.dataset.assetId)) refreshDetailRegions(a);
      }

      const busy = data.status.queued + data.status.running > 0;
      if (prevBusy && !busy && doneSinceBusy >= 2) {
        window.showToast("AI処理がすべて完了しました", "success");
      }
      if (!busy) doneSinceBusy = 0;
      prevBusy = busy;
      renderBanner(data.status);
      prev = data.assets;
    } catch (e) {
      /* 通信失敗時は次回に任せる */
    } finally {
      timer = setTimeout(poll, POLL_MS);
    }
  }

  // --- 処理開始(他スクリプトから呼ぶ) --------------------------------------

  async function enqueue(assetIds, kind) {
    const res = await fetch("/api/ai-tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_ids: assetIds, kind }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      window.showToast(`AI処理の開始に失敗しました: ${data.error || res.status}`, "error");
      return null;
    }
    const kinds = kind === "both" ? ["nsfw", "describe"] : [kind];
    const label = kinds.map((k) => LABEL[k]).join("・");
    if (data.queued === 0) {
      window.showToast(`${label}は既に待機中/実行中です`, "success");
    } else {
      window.showToast(`${label}を${data.queued}件キューに追加しました。完了したらお知らせします`, "success");
    }
    if (!data.status.worker_alive) {
      window.showToast("ワーカーが停止中です。別のターミナルで reelmilly watch を起動してください", "error");
    }
    // 完了検知のため、積んだ直後の状態を手元の前回結果へ反映しておく
    if (prev) {
      assetIds.forEach((id) => {
        if (!prev[id]) return;
        kinds.forEach((k) => {
          if (!isActive(prev[id].tasks[k])) prev[id].tasks[k] = { status: "queued" };
        });
      });
    }
    clearTimeout(timer);
    poll();
    return data;
  }

  window.aiLive = { enqueue };

  // 詳細画面などの実行ボタン
  document.querySelectorAll("[data-ai-run]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const root = document.getElementById("asset-root");
      if (root) enqueue([root.dataset.assetId], btn.dataset.aiRun);
    });
  });

  poll();
})();
