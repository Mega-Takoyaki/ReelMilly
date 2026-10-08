// AI処理(sfw/nsfw判定・説明文生成)の非同期実行を扱う。一覧・詳細画面共通。
// - 処理の開始(キューへ追加)
// - 定期ポーリングで進捗を取得し、完了したものから順に画面表示を更新する
// - 完了・失敗をトースト通知する
(function () {
  const LABEL = { nsfw: "sfw/nsfw判定", describe: "説明文生成・タグ付与", watermark: "透かし挿入", edit: "動画編集" };
  const BUSY_SHORT = { nsfw: "判定", describe: "説明生成", watermark: "透かし", edit: "動画編集" };
  const POLL_MS = 3000;

  let cards = [];
  let ids = [];

  // 画面にある作品(一覧のカード・詳細画面)を集め直す。絞り込みで一覧が差し替わるたびに呼ぶ
  function collectCards() {
    const all = Array.from(document.querySelectorAll("[data-asset-id].asset-card, #asset-root[data-asset-id]"));
    // 下へスクロールして増えた分も含め、問い合わせるのは画面の前後にあるものだけ(件数が増えてもURLが長くならない)
    const margin = window.innerHeight * 2;
    cards = all.length <= 300 ? all : all.filter((el) => {
      const r = el.getBoundingClientRect();
      return r.bottom > -margin && r.top < window.innerHeight + margin;
    }).slice(0, 300);
    ids = Array.from(new Set(cards.map((el) => el.dataset.assetId)));
  }
  collectCards();

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
    if (a.no_plan) {
      const np = document.createElement("span");
      np.className = "chip chip-noplan";
      np.dataset.tip = "投稿予定なし（SNS投稿の対象外）";
      np.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><line x1="5.6" y1="5.6" x2="18.4" y2="18.4"></line></svg>';
      box.appendChild(np);
    }
    Object.keys(CHANNELS).forEach((ch) => {
      const p = (a.posts || {})[ch];
      if (!p) return;
      const span = document.createElement("span");
      span.className = `chip chip-post post-${p.status}`;
      span.style.setProperty("--ch", CHANNELS[ch].color);
      span.dataset.channel = ch;
      span.dataset.tip = p.status === "posted"
        ? `${CHANNELS[ch].label}: 投稿済み${p.source === "manual" ? "（手動で記録）" : ""}（${fmtTime(p.posted_at)}）${p.url ? " " + p.url : ""}`
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
  const WM_POSITIONS = window.WM_POSITIONS || {};

  // 透かし入りのファイルがある作品に「透」チップを出す(_icons.htmlのwm_chipと同じ表示)
  function renderWatermark(card, a) {
    const box = card.querySelector(".asset-badges");
    if (!box) return;
    let chip = box.querySelector(".chip-wm");
    if (!a.wm) {
      if (chip) chip.remove();
      return;
    }
    if (!chip) {
      chip = document.createElement("span");
      chip.className = "chip chip-wm";
      chip.textContent = "透";
      box.appendChild(chip);
    }
    chip.dataset.tip = `透かし入り: 「${a.wm.text}」（${a.wm.label}）。投稿は透かし入りのファイルを使います`;
  }

  // 破綻画像のチップ(表示モードが「含める/のみ」のときだけ、該当する作品に出る)
  function renderBroken(card, a) {
    const box = card.querySelector(".asset-badges");
    if (!box) return;
    let chip = box.querySelector(".chip-broken");
    if (!a.broken) {
      if (chip) chip.remove();
      return;
    }
    if (!chip) {
      chip = document.createElement("span");
      chip.className = "chip chip-broken";
      chip.textContent = "破";
      chip.dataset.tip = "破綻画像（キメラ）: 既定の一覧・投稿の対象から外れています";
      box.appendChild(chip);
    }
  }

  function renderOverlay(card, a) {
    const busy = activeLabels(a);
    const sig = JSON.stringify([a.status, a.content_rating, a.content_rating_confirmed, a.nsfw_auto_rating, a.nsfw_auto_confidence, busy, a.posts, a.no_plan, a.wm, a.broken]);
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
    renderWatermark(card, a);
    renderBroken(card, a);

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
      ["live-props", "live-status", "live-posts", "live-menu"].forEach((id) => {
        const fresh = doc.getElementById(id);
        const cur = document.getElementById(id);
        if (fresh && cur) cur.innerHTML = fresh.innerHTML;
      });
      if (window.renderTagList && document.getElementById("tag-list")) window.renderTagList(a.tags);
    } catch (e) {
      /* 次回のポーリングで再試行される */
    }
  }

  // --- ジョブ状況(一覧上部) -------------------------------------------------

  const jobBox = document.getElementById("job-status");
  let upload = { active: false };
  let batchTotal = 0; // 待機・実行中が増えたときの最大件数(進捗バーの分母)
  let lastStatus = null;

  window.addEventListener("upload-progress", (e) => {
    upload = e.detail;
    if (lastStatus) renderJobStatus(lastStatus);
  });

  function chip(cls, text, tip) {
    const span = document.createElement("span");
    span.className = `job-chip ${cls}`;
    if (cls !== "upload" && cls !== "failed") {
      const dot = document.createElement("span");
      dot.className = "dot";
      span.appendChild(dot);
    }
    span.appendChild(document.createTextNode(text));
    if (tip) span.dataset.tip = tip;
    return span;
  }

  function renderJobStatus(status) {
    lastStatus = status;
    if (!jobBox) return;
    const active = status.queued + status.running;
    if (active === 0) batchTotal = 0;
    batchTotal = Math.max(batchTotal, active);

    const line = document.createElement("div");
    line.className = "job-line";
    line.appendChild(
      status.worker_alive
        ? chip("worker-on", "ワーカー稼働中", "AI処理・定期実行を行うワーカー(reelmilly watch)が動いています")
        : chip("worker-off", "ワーカー停止中", "別のターミナルで reelmilly watch を起動すると、待機中の処理が進みます")
    );
    line.appendChild(chip("running", `実行中 ${status.running}`, "いま処理しているAI処理の件数"));
    line.appendChild(chip("queued", `待機 ${status.queued}`, "順番待ちのAI処理の件数"));
    if (status.failed > 0) {
      line.appendChild(chip("failed", `失敗 ${status.failed}`, "失敗したAI処理(詳細画面に理由を表示)。再実行すると上書きされます"));
    }
    if (upload.active) {
      line.appendChild(chip("upload", `アップロード中 ${upload.count}件 ${upload.percent}%`, "画像・動画をアップロードしています"));
    }
    const next = status.next_schedule;
    if (next) {
      const span = document.createElement("span");
      span.className = "job-next";
      span.textContent = `次の定期実行: ${next.day} ${next.time}（${next.labels.join("・")}）`;
      line.appendChild(span);
    }

    const parts = [line];
    if (active > 0 || upload.active) {
      const progress = document.createElement("div");
      progress.className = "job-progress";
      const bar = document.createElement("div");
      const fill = document.createElement("span");
      if (upload.active && active === 0) {
        fill.style.width = `${upload.percent}%`;
        bar.className = "job-bar";
      } else if (status.worker_alive && batchTotal > 0) {
        fill.style.width = `${Math.round(((batchTotal - active) / batchTotal) * 100)}%`;
        bar.className = "job-bar";
      } else {
        bar.className = "job-bar indeterminate";
      }
      bar.appendChild(fill);
      progress.appendChild(bar);

      const detail = document.createElement("div");
      detail.className = "job-detail";
      const texts = [];
      if (status.current) texts.push(`処理中: ${short(status.current.asset_id)}（${LABEL[status.current.kind]}）`);
      if ((status.upcoming || []).length > 0) {
        const more = status.queued - status.upcoming.length;
        texts.push(
          "次: " + status.upcoming.map((t) => `${short(t.asset_id)}（${BUSY_SHORT[t.kind]}）`).join("、") +
            (more > 0 ? ` ほか${more}件` : "")
        );
      }
      if (batchTotal > 0 && active > 0) texts.push(`${batchTotal - active}/${batchTotal}件完了`);
      detail.textContent = texts.join("　／　");
      progress.appendChild(detail);
      parts.push(progress);
    }
    jobBox.replaceChildren(...parts);
  }

  // --- 完了検知・通知 -------------------------------------------------------

  function detectTransitions(assets) {
    // 処理の種類(LABELのキー)ごとに入れ物を用意する。種類を足しても、ここは直さなくてよい
    const completed = Object.fromEntries(Object.keys(LABEL).map((k) => [k, []]));
    const failed = Object.fromEntries(Object.keys(LABEL).map((k) => [k, []]));
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

  let pollGen = 0; // poll()が重なって呼ばれても、ポーリングのループは1本だけにする

  async function poll() {
    const myGen = ++pollGen;
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
      renderJobStatus(data.status);
      prev = data.assets;
    } catch (e) {
      console.error("ai-live: 状況の更新に失敗しました", e); // 通信失敗・表示更新の例外は次回に任せる
    } finally {
      clearTimeout(timer);
      if (myGen === pollGen) timer = setTimeout(poll, POLL_MS);
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
    track(assetIds, kinds);
    return data;
  }

  // 完了検知のため、積んだ直後の状態を手元の前回結果へ反映し、すぐに状況を取り直す
  function track(assetIds, kinds) {
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
  }

  window.aiLive = { enqueue, track };

  // 続きが増えた/スクロールが止まったときは、問い合わせ対象だけ更新する(完了検知の比較元は残す)
  let scrollTimer = null;
  function refreshVisible() {
    collectCards();
    clearTimeout(timer);
    poll();
  }
  window.addEventListener("grid-appended", refreshVisible);
  window.addEventListener("scroll", () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      if (document.querySelectorAll(".asset-card").length > 300) refreshVisible();
    }, 500);
  }, { passive: true });

  window.addEventListener("grid-updated", () => {
    collectCards();
    prev = null; // 表示する作品が変わったので、完了検知の比較元もリセットする
    clearTimeout(timer);
    poll();
  });

  // 詳細画面の実行ボタン(メニューの中身は完了時に差し替わるため、documentで受ける)
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-ai-run]");
    const root = document.getElementById("asset-root");
    if (!btn || !root || btn.disabled) return;
    enqueue([root.dataset.assetId], btn.dataset.aiRun);
    const menu = document.getElementById("asset-menu");
    if (menu) menu.open = false;
  });

  poll();
})();
