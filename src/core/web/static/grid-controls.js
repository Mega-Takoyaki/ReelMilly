// 一覧画面: サムネイルサイズのスライダー。選択値はlocalStorageに保存し次回訪問時も維持する。
(function () {
  const slider = document.getElementById("thumb-size-slider");
  const grid = document.querySelector(".asset-grid");
  if (!slider || !grid) return;

  const STORAGE_KEY = "reelmilly:thumbSize";

  function applySize(px) {
    grid.style.setProperty("--thumb-size", `${px}px`);
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

  slider.addEventListener("input", () => {
    applySize(slider.value);
    try {
      localStorage.setItem(STORAGE_KEY, slider.value);
    } catch (err) {
      // private browsing等でlocalStorageが使えない場合は無視する
    }
  });
})();
