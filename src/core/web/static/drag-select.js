// 一覧・ごみ箱: マウスのドラッグで、複数のサムネイルをまとめて選択する。
// - チェックボックスがオンのサムネイルから、ドラッグを始める(オフのサムネイルからは始まらない)
// - ドラッグでできる四角形(範囲選択)にふれたサムネイルが、チェックされる。範囲から外すと、そのドラッグで足した分は外れる
// - マウスを離しても選択は残り、あらためてオンのサムネイルからドラッグすれば、追加で選択できる
// - 画面の上下の端にポインタを寄せると、スクロールしながら選択できる
// 選択の状態は、チェックボックスのchangeイベントで、一括操作(bulk.js / trash.js)へ伝える。
(function () {
  const THRESHOLD = 6; // これ以上動いたら、ドラッグとみなす(クリックと区別する)
  const EDGE = 48; // 画面の端からこの距離に入ると、自動でスクロールする

  let drag = null;
  let rectEl = null;
  let raf = null;

  const checkboxOf = (card) => card.querySelector(".asset-checkbox");

  function setChecked(card, value) {
    const cb = checkboxOf(card);
    if (!cb || cb.checked === value) return;
    cb.checked = value;
    cb.dispatchEvent(new Event("change", { bubbles: true })); // 一括操作のバーの件数などを更新させる
  }

  function cards() {
    return Array.from(document.querySelectorAll(".asset-card"));
  }

  // ドラッグの四角形(ページ座標)にふれているサムネイルを、チェックする
  function updateSelection() {
    const x1 = Math.min(drag.startX, drag.pageX);
    const x2 = Math.max(drag.startX, drag.pageX);
    const y1 = Math.min(drag.startY, drag.pageY);
    const y2 = Math.max(drag.startY, drag.pageY);

    cards().forEach((card) => {
      const r = card.getBoundingClientRect();
      const left = r.left + window.scrollX;
      const top = r.top + window.scrollY;
      const hit = left < x2 && left + r.width > x1 && top < y2 && top + r.height > y1;
      const id = card.dataset.assetId;
      if (hit) {
        if (!checkboxOf(card).checked) drag.added.add(id);
        setChecked(card, true);
      } else if (drag.added.has(id)) {
        drag.added.delete(id); // このドラッグで足したものだけ、範囲から外れたら戻す
        setChecked(card, false);
      }
    });

    rectEl.style.left = `${x1 - window.scrollX}px`;
    rectEl.style.top = `${y1 - window.scrollY}px`;
    rectEl.style.width = `${x2 - x1}px`;
    rectEl.style.height = `${y2 - y1}px`;
  }

  function autoScroll() {
    if (!drag || !drag.moved) return;
    let dy = 0;
    if (drag.clientY < EDGE) dy = -Math.ceil((EDGE - drag.clientY) / 4);
    else if (drag.clientY > window.innerHeight - EDGE) dy = Math.ceil((drag.clientY - (window.innerHeight - EDGE)) / 4);
    if (dy) {
      window.scrollBy(0, dy);
      drag.pageY = drag.clientY + window.scrollY;
      updateSelection();
    }
    raf = requestAnimationFrame(autoScroll);
  }

  function finish() {
    if (!drag) return;
    if (drag.moved) {
      // ドラッグの直後に出るclickで、プレビューが開いたり、選択が反転したりしないようにする
      const stop = (e) => { e.stopPropagation(); e.preventDefault(); };
      window.addEventListener("click", stop, { capture: true, once: true });
      setTimeout(() => window.removeEventListener("click", stop, true), 0);
    }
    if (rectEl) rectEl.remove();
    rectEl = null;
    cancelAnimationFrame(raf);
    document.body.classList.remove("is-drag-selecting");
    drag = null;
  }

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const card = e.target.closest(".asset-card");
    if (!card) return;
    // ボタン・リンク・チェックボックスなどの操作は、これまでどおり
    if (e.target.closest("input, button, a, label, select, textarea")) return;
    const cb = checkboxOf(card);
    if (!cb || !cb.checked) return; // チェックがオンのサムネイルからだけ、ドラッグ選択を始める

    drag = {
      startX: e.pageX, startY: e.pageY, pageX: e.pageX, pageY: e.pageY,
      clientX: e.clientX, clientY: e.clientY, moved: false, added: new Set(),
    };
    e.preventDefault(); // 文字・画像の選択や、ブラウザ標準の画像ドラッグを始めない
  });

  document.addEventListener("mousemove", (e) => {
    if (!drag) return;
    drag.clientX = e.clientX;
    drag.clientY = e.clientY;
    drag.pageX = e.pageX;
    drag.pageY = e.pageY;
    if (!drag.moved) {
      if (Math.hypot(e.pageX - drag.startX, e.pageY - drag.startY) < THRESHOLD) return;
      drag.moved = true;
      rectEl = document.createElement("div");
      rectEl.className = "drag-select-rect";
      document.body.appendChild(rectEl);
      document.body.classList.add("is-drag-selecting");
      raf = requestAnimationFrame(autoScroll);
    }
    if (e.buttons === 0) { // ボタンを離したのにmouseupを受け取れなかった場合(ウィンドウ外など)
      finish();
      return;
    }
    updateSelection();
  });

  document.addEventListener("mouseup", finish);
  window.addEventListener("blur", finish);
})();
