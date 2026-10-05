// AI処理(sfw/nsfw判定・説明文生成)の非同期実行を扱う。一覧・詳細画面共通。
// - 処理の開始(キューへ追加)
// - 定期ポーリングで進捗を取得し、完了したものから順に画面表示を更新する
// - 完了・失敗をトースト通知する
(function () {
  const LABEL = { nsfw: "sfw/nsfw判定", describe: "説明文生成・タグ付与" };
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

  const STATUS_LABELS = {
    analyzing: "ANALYZING（未処理・分析中）",
    pending_approval: "PENDING_APPROVAL（承認待ち）",
    ready: "READY（承認済み・投稿準備完了）",
  };
  const CHANNELS = window.POST_CHANNELS || {};

  function fmtTime(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return iso || "";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  // 投稿先ごとのフラグ(_icons.htmlのpost_marksと同じ表示)。投稿済み=色の塗り / 失敗=点線の枠
  function renderPosts(card, a) {
    const box = card.querySelector(".asset-posts");
    if (!box) return;
    box.innerHTML = "";
    Object.keys(CHANNELS).forEach((ch) => {
      const p = (a.posts || {})[ch];
      if (!p) return;
      const span = document.createElement("span");
      span.className = `chip chip-post post-${p.status}`;
      span.style.setProperty("--ch", CHANNELS[ch].color);
      span.dataset.channel = ch;
      span.dataset.tip = p.status === "posted"
        ? `${CHANNELS[ch].label}: 投稿済み（${fmtTime(p.posted_at)}）${p.url ? " " + p.url : ""}`
        : `${CHANNELS[ch].label}: 投稿に失敗（${p.error || "理由不明"}）`;
      span.textContent = CHANNELS[ch].letter;
      box.appendChild(span);
    });
  }

  function activeLabels(a) {
    const words = { queued: "待機中", running: "実行中" };
    return Object.keys(a.tasks || {})
      .filter((k) => isActive(a.tasks[k]))
      .map((k) => `${LABEL[k]}（${words[a.tasks[k].status]}）`);
  }

  // 一覧のサムネイルに重ねるアイコン(sfw/nsfw・ステータス)を、_icons.htmlと同じ判定で描き直す
  function renderOverlay(card, a) {
    const busy = activeLabels(a);
    const sig = JSON.stringify([a.status, a.content_rating, a.content_rating_confirmed, a.nsfw_auto_rating, a.nsfw_auto_confidence, busy, a.posts]);
    if (card.dataset.sig === sig) return; // 変化が無ければ触らない(ツールチップのちらつき防止)
    card.dataset.sig = sig;

    let state, letter, tip;
    if (a.content_rating_confirmed && a.content_rating) {
      state = { sfw: "ok-sfw", suggestive: "ok-sug", explicit: "ok-nsfw" }[a.content_rating] || "ok-sfw";
      letter = a.content_rating === "explicit" ? "N" : "S";
      tip = "承認済み: " + a.content_rating + (a.nsfw_auto_rating ? `（AI判定: ${a.nsfw_auto_rating}）` : "");
    } else if (a.nsfw_auto_rating) {
      state = a.nsfw_auto_rating === "nsfw" ? "auto-nsfw" : "auto-sfw";
      letter = a.nsfw_auto_rating === "nsfw" ? "N" : "S";
      tip = `AI自動判定: ${a.nsfw_auto_rating.toUpperCase()}（確信度 ${Number(a.nsfw_auto_confidence || 0).toFixed(2)}）・人の承認はまだです`;
    } else {
      state = "none";
      letter = "?";
      tip = "未処理（sfw/nsfw判定がまだありません）";
    }
    const nsfwBusy = isActive((a.tasks || {}).nsfw);
    const rating = card.querySelector(".chip-rating");
    if (rating) {
      rating.className = `chip chip-rating rating-${state}${nsfwBusy ? " is-busy" : ""}`;
      rating.textContent = letter;
      rating.dataset.tip = tip + (nsfwBusy ? "　／判定を実行中です" : "");
    }

    renderPosts(card, a);

    const dot = card.querySelector(".status-dot");
    if (dot) {
      dot.className = `status-dot status-${a.status}${busy.length ? " is-busy" : ""}`;
      dot.dataset.tip = (STATUS_LABELS[a.status] || a.status.toUpperCase()) + (busy.length ? "　／AI処理: " + busy.join("、") : "");
      dot.setAttribute("aria-label", a.status);
    }
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
        if (el.classList.contains("asset-card")) renderOverlay(el, a);
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
