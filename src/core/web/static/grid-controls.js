// 一覧画面: サムネイルサイズのスライダー。選択値はlocalStorageに保存し次回訪問時も維持する。
(function () {
  const slider = document.getElementById("thumb-size-slider");
  if (!slider) return;

  const STORAGE_KEY = "reelmilly:thumbSize";

  function applySize(px) {
    // 絞り込みで一覧が差し替わると.asset-gridも新しくなるため、その都度探す
    document.querySelectorAll(".asset-grid").forEach((grid) => grid.style.setProperty("--thumb-size", `${px}px`));
  }

  let saved = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch (err) {
    saved = null;
  }
  if (saved) {
    slider.value = saved;
  }
  applySize(slider.value);

  window.addEventListener("grid-updated", () => applySize(slider.value));

  slider.addEventListener("input", () => {
    applySize(slider.value);
    try {
      localStorage.setItem(STORAGE_KEY, slider.value);
    } catch (err) {
      // private browsing等でlocalStorageが使えない場合は無視する
    }
  });
})();
