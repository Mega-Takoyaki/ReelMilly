// 一覧: 下へスクロールして、上部のメニュー(ジョブ状況・アップロード・検索と絞り込み・サムネイルサイズ)が画面の外へ出たら、
// 最上部の中央に小さなタブを残す。タブを押すと、そのメニューが重ねて(ホバーで)開き、スクロールした先でも操作できる。
// - もう一度タブを押す・メニューの外を押す・Escで閉じる。一番上へ戻ると、元の位置に戻る
(function () {
  const wrap = document.getElementById("top-controls-wrap");
  const panel = document.getElementById("top-controls");
  if (!wrap || !panel) return;

  const tab = document.createElement("button");
  tab.type = "button";
  tab.id = "controls-tab";
  tab.className = "controls-tab";
  tab.hidden = true;
  tab.setAttribute("aria-expanded", "false");
  tab.innerHTML = '<span aria-hidden="true">▾</span> メニュー・絞り込み';
  document.body.appendChild(tab);

  const topbar = document.querySelector(".topbar");
  const barHeight = () => (topbar ? topbar.offsetHeight : 0);
  let open = false;

  function setOpen(value) {
    if (open === value) return;
    open = value;
    if (open) {
      // 重ねて表示する間も、元の場所の高さは保つ(下の一覧が、ずれないように)
      wrap.style.height = `${wrap.offsetHeight}px`;
      panel.style.setProperty("--dock-top", `${barHeight()}px`);
      panel.classList.add("is-floating");
    } else {
      panel.classList.remove("is-floating");
      wrap.style.height = "";
    }
    tab.setAttribute("aria-expanded", String(open));
    tab.classList.toggle("is-open", open);
  }

  function update() {
    const scrolledOut = wrap.getBoundingClientRect().bottom < barHeight();
    tab.hidden = !scrolledOut;
    tab.style.top = `${barHeight()}px`;
    if (!scrolledOut) setOpen(false);
  }

  tab.addEventListener("click", () => setOpen(!open));
  document.addEventListener("mousedown", (e) => {
    if (open && !panel.contains(e.target) && !tab.contains(e.target) && !e.target.closest(".dialog, dialog")) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && open && !document.querySelector("dialog[open]")) setOpen(false);
  });
  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  update();
})();
