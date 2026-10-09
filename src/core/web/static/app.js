// 全画面共通のUIユーティリティ(トースト通知・確認ダイアログ)。
// alert()/confirm()より画面に馴染む見た目にするための薄いラッパー。
window.showToast = function showToast(message, type) {
  let stack = document.getElementById("toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "toast-stack";
    document.body.appendChild(stack);
  }
  const toast = document.createElement("div");
  toast.className = `toast toast-${type === "error" ? "error" : "success"}`;
  toast.textContent = message;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 3200);
};

window.confirmDialog = function confirmDialog(message) {
  return new Promise((resolve) => {
    if (typeof HTMLDialogElement === "undefined") {
      resolve(window.confirm(message));
      return;
    }
    const dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    dialog.innerHTML = `
      <p></p>
      <div class="confirm-actions">
        <button type="button" class="btn-ghost" data-action="cancel">キャンセル</button>
        <button type="button" class="btn-danger" data-action="ok">実行する</button>
      </div>
    `;
    dialog.querySelector("p").textContent = message;

    function cleanup(result) {
      dialog.close();
      dialog.remove();
      resolve(result);
    }

    dialog.querySelector('[data-action="cancel"]').addEventListener("click", () => cleanup(false));
    dialog.querySelector('[data-action="ok"]').addEventListener("click", () => cleanup(true));
    dialog.addEventListener("cancel", () => cleanup(false));

    document.body.appendChild(dialog);
    dialog.showModal();
  });
};

// 設定項目の「ⓘ」アイコン: ホバー/フォーカスで説明を表示し、クリックで固定する。
// 固定した説明は、説明欄の外をクリックする(またはEscを押す)まで表示し続ける。
(function () {
  const icons = document.querySelectorAll(".info-icon");
  if (icons.length === 0) return;

  const pop = document.createElement("div");
  pop.className = "info-pop";
  pop.setAttribute("role", "tooltip");
  pop.hidden = true;
  document.body.appendChild(pop);

  let current = null;
  let pinned = false;

  function place(icon) {
    const r = icon.getBoundingClientRect();
    pop.style.maxWidth = Math.min(340, window.innerWidth - 24) + "px";
    const pr = pop.getBoundingClientRect();
    let left = Math.max(12, Math.min(r.left + r.width / 2 - pr.width / 2, window.innerWidth - pr.width - 12));
    let top = r.bottom + 8;
    if (top + pr.height > window.innerHeight - 8 && r.top - pr.height - 8 > 8) top = r.top - pr.height - 8;
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  // モーダルのダイアログは、画面の最前面の層に出るため、body直下の吹き出しは、その後ろに隠れる。ダイアログの中に移す
  function host(el) {
    const parent = el.closest("dialog[open]") || document.body;
    if (pop.parentNode !== parent) parent.appendChild(pop);
  }

  function show(icon) {
    current = icon;
    host(icon);
    pop.textContent = icon.dataset.info;
    pop.hidden = false;
    place(icon);
  }

  function hide() {
    pop.hidden = true;
    pop.classList.remove("pinned");
    if (current) current.classList.remove("active");
    current = null;
    pinned = false;
  }

  icons.forEach((icon) => {
    icon.addEventListener("mouseenter", () => { if (!pinned) show(icon); });
    icon.addEventListener("mouseleave", () => { if (!pinned) hide(); });
    icon.addEventListener("focus", () => { if (!pinned) show(icon); });
    icon.addEventListener("blur", () => { if (!pinned) hide(); });
    icon.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (pinned && current === icon) {
        hide(); // 固定中に同じアイコンをもう一度押すと解除
        return;
      }
      if (current) current.classList.remove("active");
      show(icon);
      pinned = true;
      pop.classList.add("pinned");
      icon.classList.add("active");
    });
  });

  // 固定中は説明欄の中のクリック(文字の選択など)では閉じず、外側のクリックで閉じる
  document.addEventListener("click", (e) => {
    if (pinned && !pop.contains(e.target)) hide();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pop.hidden) hide();
  });
  window.addEventListener("resize", () => { if (current) place(current); });
  window.addEventListener("scroll", () => { if (current) place(current); }, true);
})();

// data-tip属性を持つ要素(一覧のアイコンなど)に、即時表示のツールチップを出す。
// 一覧のアイコンはJSで再描画されるため、イベント委譲で扱う。
(function () {
  const pop = document.createElement("div");
  pop.className = "tip-pop";
  pop.hidden = true;
  document.body.appendChild(pop);
  let current = null;

  function place(el) {
    const r = el.getBoundingClientRect();
    const pr = pop.getBoundingClientRect();
    const left = Math.max(6, Math.min(r.left + r.width / 2 - pr.width / 2, window.innerWidth - pr.width - 6));
    let top = r.top - pr.height - 6;
    if (top < 6) top = r.bottom + 6;
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest ? e.target.closest("[data-tip]") : null;
    if (!el || el === current) return;
    current = el;
    // モーダルのダイアログの中では、ダイアログの中に出す(でないと、最前面の層の後ろに隠れる)
    const parent = el.closest("dialog[open]") || document.body;
    if (pop.parentNode !== parent) parent.appendChild(pop);
    pop.textContent = el.dataset.tip;
    pop.hidden = false;
    place(el);
  });
  document.addEventListener("mouseout", (e) => {
    if (!current) return;
    const to = e.relatedTarget;
    if (to && current.contains(to)) return;
    pop.hidden = true;
    current = null;
  });
  window.addEventListener("scroll", () => { pop.hidden = true; current = null; }, true);
})();

// 一覧の複数選択フィルタ: 外側をクリックするか、別のフィルタを開いたら閉じる(項目内はチェックだけで適用はしない)
(function () {
  const panels = Array.from(document.querySelectorAll(".ms details"));
  if (panels.length === 0) return;
  document.addEventListener("click", (e) => {
    panels.forEach((d) => { if (d.open && !d.contains(e.target)) d.open = false; });
  });
  // PC画面で、右寄りのフィルタの選択パネルが画面の右に切れないよう、はみ出す分だけ左へずらす
  // (スマホでは、CSSで画面下に固定表示するので、何もしない)
  function fitPanel(d) {
    const panel = d.querySelector(".ms-panel");
    if (!panel) return;
    panel.style.left = "";
    if (getComputedStyle(panel).position !== "absolute") return;
    const rect = panel.getBoundingClientRect();
    const margin = 8;
    let shift = 0;
    if (rect.right > window.innerWidth - margin) shift = window.innerWidth - margin - rect.right;
    if (rect.left + shift < margin) shift = margin - rect.left; // 左にも切れないようにする(幅が足りない場合は左を優先)
    if (shift) panel.style.left = `${shift}px`;
  }

  panels.forEach((d) => d.addEventListener("toggle", () => {
    if (d.open) {
      panels.forEach((o) => { if (o !== d) o.open = false; });
      fitPanel(d);
    }
  }));
  window.addEventListener("resize", () => panels.forEach((d) => { if (d.open) fitPanel(d); }));
  // 2階層(ファイルタイプ): 親(種別)のチェックで配下の拡張子をすべて入り切りし、
  // 配下がすべて選ばれたら親にもチェックを付ける。一部だけのときは親を「一部選択」表示にする
  function syncTree(ms, changed) {
    ms.querySelectorAll(".tree-node").forEach((node) => {
      const parent = node.querySelector('input[name="kind"]');
      const children = Array.from(node.querySelectorAll('input[name="ext"]'));
      if (changed === parent) children.forEach((c) => (c.checked = parent.checked));
      const checked = children.filter((c) => c.checked).length;
      parent.checked = children.length > 0 && checked === children.length;
      parent.indeterminate = checked > 0 && checked < children.length;
    });
  }
  document.querySelectorAll(".ms[data-tree]").forEach((ms) => syncTree(ms, null));

  // チェックを入れた数を、絞り込む前でも見出しに反映する
  document.querySelectorAll(".ms").forEach((ms) => {
    const count = ms.querySelector(".ms-count");
    ms.addEventListener("change", (e) => {
      if (!ms.querySelector('input[type="checkbox"]')) return; // ラジオだけの項目(破綻画像)は、filters.jsが表示を更新する
      if (ms.hasAttribute("data-tree")) syncTree(ms, e.target);
      const selector = ms.hasAttribute("data-tree") ? 'input[name="ext"]:checked' : 'input[type="checkbox"]:checked';
      const n = ms.querySelectorAll(selector).length;
      count.textContent = n;
      count.hidden = n === 0;
    });
  });
})();
