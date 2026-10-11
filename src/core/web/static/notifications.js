// 通知: 画面右上のベル(未読の印・最新10件のポップアップ)と、設定画面「通知・ログ」タブの全件の履歴。
(function () {
  const POLL_MS = 15000;
  const bell = document.getElementById("notif-bell");
  const dot = document.getElementById("notif-dot");
  const pop = document.getElementById("notif-pop");
  const list = document.getElementById("notif-list");
  if (!bell || !pop || !list) return;

  function pad(n) { return String(n).padStart(2, "0"); }

  function timeText(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const minutes = Math.round((Date.now() - d.getTime()) / 60000);
    if (minutes < 1) return "たった今";
    if (minutes < 60) return `${minutes}分前`;
    const today = new Date();
    const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return d.toDateString() === today.toDateString() ? `今日 ${hhmm}` : `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${hhmm}`;
  }

  // 通知1件の表示。ベルのポップアップと、履歴の一覧で共用する
  function renderItem(n) {
    const li = document.createElement("li");
    li.className = `notif-item level-${n.level}${n.read ? "" : " unread"}`;
    const mark = document.createElement("span");
    mark.className = "notif-mark";
    const body = document.createElement("div");
    body.className = "notif-body";
    const title = document.createElement("div");
    title.className = "notif-title";
    title.textContent = n.title;
    const meta = document.createElement("div");
    meta.className = "notif-meta";
    meta.textContent = `${n.kind_label}　${timeText(n.created_at)}`;
    body.append(title);
    if (n.body) {
      const text = document.createElement("div");
      text.className = "notif-text";
      text.textContent = n.body;
      body.append(text);
    }
    body.append(meta);
    const actions = document.createElement("div");
    actions.className = "notif-actions";
    if (n.action === "duplicates") {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn-ghost-inline";
      b.textContent = "確認する";
      b.addEventListener("click", () => {
        pop.hidden = true;
        if (window.openDuplicates) window.openDuplicates();
      });
      actions.append(b);
    }
    if (n.url) {
      const link = document.createElement("a");
      link.href = n.url;
      link.target = "_blank";  // 投稿先は、別タブで開く
      link.rel = "noopener noreferrer";
      link.textContent = "投稿を開く ↗";
      actions.append(link);
    }
    if (n.asset_id) {
      const a = document.createElement("a");
      a.href = `/assets/${n.asset_id}`;
      a.textContent = "作品を開く";
      actions.append(a);
    }
    li.append(mark, body, actions);
    return li;
  }

  async function fetchNotifications(params) {
    const res = await fetch(`/api/notifications?${new URLSearchParams(params)}`);
    if (!res.ok) throw new Error(res.status);
    return res.json();
  }

  function setUnread(count) {
    dot.hidden = count === 0;
    bell.classList.toggle("has-unread", count > 0);
    bell.setAttribute("aria-label", count > 0 ? `通知（未読${count}件）` : "通知");
  }

  async function refreshDot() {
    if (document.hidden) return;
    try {
      const data = await fetchNotifications({ limit: 1 });
      setUnread(data.unread);
      if (!pop.hidden) loadPopup(false);
    } catch (e) { /* 通信失敗時は、次回に任せる */ }
  }

  async function loadPopup(markRead) {
    try {
      const data = await fetchNotifications({ limit: 10 });
      list.replaceChildren(...data.items.map(renderItem));
      if (data.items.length === 0) list.innerHTML = '<li class="notif-empty">通知はまだありません</li>';
      if (markRead && data.unread > 0) {
        // 開いて見た時点で既読にする(この表示の間は、未読だった分の強調は残す)
        await fetch("/api/notifications/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ all: true }) });
        setUnread(0);
      }
    } catch (e) {
      list.innerHTML = '<li class="notif-empty">通知を取得できませんでした</li>';
    }
  }

  bell.addEventListener("click", (e) => {
    e.stopPropagation();
    pop.hidden = !pop.hidden;
    bell.setAttribute("aria-expanded", String(!pop.hidden));
    if (!pop.hidden) loadPopup(true);
  });
  document.getElementById("notif-readall").addEventListener("click", async () => {
    await fetch("/api/notifications/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ all: true }) });
    setUnread(0);
    list.querySelectorAll(".unread").forEach((el) => el.classList.remove("unread"));
  });
  document.addEventListener("click", (e) => {
    if (!pop.hidden && !pop.contains(e.target)) { pop.hidden = true; bell.setAttribute("aria-expanded", "false"); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pop.hidden) { pop.hidden = true; bell.setAttribute("aria-expanded", "false"); }
  });

  // --- 設定画面の「通知・ログ」(全件の履歴) ---
  const logList = document.getElementById("log-list");
  if (logList) {
    const kindSelect = document.getElementById("log-kind");
    const more = document.getElementById("log-more");
    const total = document.getElementById("log-total");
    let offset = 0;
    const PAGE = 50;

    async function loadLog(reset) {
      if (reset) { offset = 0; logList.replaceChildren(); }
      const params = { limit: PAGE, offset };
      if (kindSelect.value) params.kind = kindSelect.value;
      try {
        const data = await fetchNotifications(params);
        data.items.forEach((n) => logList.append(renderItem(n)));
        offset += data.items.length;
        more.hidden = offset >= data.total;
        total.textContent = `${data.total}件`;
        if (data.items.length === 0 && reset) logList.innerHTML = '<li class="notif-empty">履歴はまだありません</li>';
      } catch (e) {
        logList.innerHTML = '<li class="notif-empty">履歴を取得できませんでした</li>';
      }
    }

    kindSelect.addEventListener("change", () => loadLog(true));
    more.addEventListener("click", () => loadLog(false));
    document.querySelector('[data-tab="sec-log"]').addEventListener("click", () => loadLog(true));
    if (location.hash === "#sec-log") loadLog(true);
  }

  refreshDot();
  setInterval(refreshDot, POLL_MS);
  window.Notifications = { refresh: refreshDot };
})();
