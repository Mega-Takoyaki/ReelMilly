// 一覧画面: サムネイルクリックで画像/動画をポップアップ(dialog)プレビュー表示する。
// 詳細画面への遷移は.asset-detail-linkアイコン経由のみ(サムネイル本体のクリックとは分離)。
(function () {
  const dialog = document.getElementById("lightbox");
  const mediaContainer = dialog ? dialog.querySelector(".lightbox-media") : null;
  const closeButton = dialog ? dialog.querySelector(".lightbox-close") : null;

  if (!dialog || !mediaContainer || !closeButton) return;

  function openLightbox(src, kind, alt) {
    mediaContainer.innerHTML = "";
    if (kind === "video") {
      const video = document.createElement("video");
      video.src = src;
      video.controls = true;
      mediaContainer.appendChild(video);
    } else {
      const img = document.createElement("img");
      img.src = src;
      img.alt = alt || "";
      mediaContainer.appendChild(img);
    }
    dialog.showModal();
  }

  function closeLightbox() {
    if (dialog.open) dialog.close();
  }

  document.querySelectorAll(".asset-media").forEach((el) => {
    el.addEventListener("click", () => {
      openLightbox(el.dataset.src, el.dataset.kind, el.dataset.alt);
    });
  });

  document.querySelectorAll(".asset-detail-link").forEach((link) => {
    // 詳細アイコンのクリックはサムネイルのクリック(ライトボックス表示)へ伝播させない
    link.addEventListener("click", (e) => e.stopPropagation());
  });

  closeButton.addEventListener("click", closeLightbox);

  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) closeLightbox();
  });

  dialog.addEventListener("close", () => {
    mediaContainer.innerHTML = "";
  });
})();
