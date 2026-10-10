// 一覧画面: サムネイルクリックで画像/動画をポップアップ(dialog)プレビュー表示する。
// 詳細画面への遷移は.asset-detail-linkアイコン経由のみ(サムネイル本体のクリックとは分離)。
// 画像は再クリックで閉じ、倍率スライダーで拡大縮小できる。動画は音声オフで自動再生し、閉じると停止する。
(function () {
  const dialog = document.getElementById("lightbox");
  const mediaContainer = dialog ? dialog.querySelector(".lightbox-media") : null;
  const closeButton = dialog ? dialog.querySelector(".lightbox-close") : null;
  const zoomBar = dialog ? dialog.querySelector(".lightbox-zoom") : null;
  const zoomSlider = zoomBar ? zoomBar.querySelector("input") : null;
  const zoomOutput = zoomBar ? zoomBar.querySelector("output") : null;

  const captionEl = dialog ? dialog.querySelector(".lightbox-caption") : null;
  const prevButton = dialog ? dialog.querySelector(".lightbox-prev") : null;
  const nextButton = dialog ? dialog.querySelector(".lightbox-next") : null;
  let items = [];
  let index = -1;

  if (!dialog || !mediaContainer || !closeButton || !zoomBar || !zoomSlider || !prevButton || !nextButton || !captionEl) return;

  let baseWidth = 0;

  function applyZoom() {
    const img = mediaContainer.querySelector("img");
    const percent = Number(zoomSlider.value);
    zoomOutput.textContent = percent + "%";
    if (!img || !baseWidth) return;
    // 倍率100%=画面に収まる大きさ。拡大時はコンテナ内でスクロールできる
    img.style.maxWidth = "none";
    img.style.maxHeight = "none";
    img.style.width = (baseWidth * percent) / 100 + "px";
  }

  function show(i) {
    index = i;
    const item = items[i];
    prevButton.hidden = i <= 0;
    nextButton.hidden = i >= items.length - 1;
    openLightbox(item.src, item.kind, item.alt, item.caption);
  }

  function step(delta) {
    const next = index + delta;
    if (next >= 0 && next < items.length) show(next);
  }

  // プレビューを開いても、一覧のスクロール位置が最上部へ戻らないようにする
  // (モーダルを開くときに、ブラウザがページを先頭へ動かしてしまうことがあるため、開く前の位置へ戻す)
  function openModal() {
    if (dialog.open) return;
    const x = window.scrollX;
    const y = window.scrollY;
    dialog.showModal();
    if (window.scrollY !== y) window.scrollTo(x, y);
  }

  function openLightbox(src, kind, alt, caption) {
    captionEl.textContent = caption || "";
    captionEl.hidden = !caption;
    const video = mediaContainer.querySelector("video");
    if (video) video.pause();
    mediaContainer.innerHTML = "";
    zoomSlider.value = 100;
    baseWidth = 0;
    if (kind === "video") {
      zoomBar.hidden = true;
      const video = document.createElement("video");
      video.src = src;
      video.controls = true;
      video.muted = true;
      video.autoplay = true;
      video.playsInline = true;
      mediaContainer.appendChild(video);
      openModal();
      video.play().catch(() => {});
    } else {
      zoomBar.hidden = false;
      zoomOutput.textContent = "100%";
      const img = document.createElement("img");
      img.alt = alt || "";
      img.addEventListener("load", () => {
        baseWidth = img.clientWidth;
        applyZoom();
      });
      img.addEventListener("click", closeLightbox);
      img.src = src;
      mediaContainer.appendChild(img);
      openModal();
    }
  }

  function closeLightbox() {
    if (dialog.open) dialog.close();
  }

  // サムネイルのクリックを結び付ける。絞り込みで一覧が差し替わるたびに呼ぶ
  function bindItems() {
    const els = Array.from(document.querySelectorAll(".asset-media"));
    items = els.map((el) => ({ el, src: el.dataset.src, kind: el.dataset.kind, alt: el.dataset.alt }));
    els.forEach((el) => {
      if (el.dataset.lbBound) return; // 続きの追加で呼ばれても、結び付け済みのものは重ねない
      el.dataset.lbBound = "1";
      el.addEventListener("click", () => show(items.findIndex((x) => x.el === el)));
    });
    document.querySelectorAll(".asset-detail-link").forEach((link) => {
      if (link.dataset.lbBound) return;
      link.dataset.lbBound = "1";
      // 詳細アイコンのクリックはサムネイルのクリック(ライトボックス表示)へ伝播させない
      link.addEventListener("click", (e) => e.stopPropagation());
    });
  }
  bindItems();
  window.addEventListener("grid-appended", bindItems);
  window.addEventListener("grid-updated", () => {
    if (dialog.open) dialog.close();
    bindItems();
  });

  prevButton.addEventListener("click", () => step(-1));
  nextButton.addEventListener("click", () => step(1));
  dialog.addEventListener("keydown", (e) => {
    if (e.target === zoomSlider) return; // スライダー操作中の左右キーは倍率調整に使う
    if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
  });

  // 一覧以外(詳細画面の「透かし入りを見る」など)から、1枚だけ同じポップアップで表示する
  window.openPreview = function (src, kind, alt) {
    index = -1;
    items = [];
    prevButton.hidden = true;
    nextButton.hidden = true;
    openLightbox(src, kind || "image", alt);
  };

  // 詳細画面のファイル一覧(原本と加工版)など、並んだ複数のファイルを、左右キー・矢印で順に見られるようにして開く。
  // list: [{src, kind, alt, caption}]、start: 最初に出す番号
  window.openPreviewList = function (list, start) {
    items = list;
    show(Math.min(Math.max(start || 0, 0), list.length - 1));
  };

  zoomSlider.addEventListener("input", applyZoom);
  closeButton.addEventListener("click", closeLightbox);

  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) closeLightbox();
  });

  dialog.addEventListener("close", () => {
    const video = mediaContainer.querySelector("video");
    if (video) video.pause();
    mediaContainer.innerHTML = "";
    index = -1;
    captionEl.hidden = true;
  });
})();
