// 一覧の表示オプション: サムネイルサイズ(スライダー)と、形状のトグル(コンパクト/フル)。
// - コンパクト(既定): 正方形に切り抜いて並べる / フル: 画像・動画の縦横比のまま、列ごとに縦に積む
// 選択はlocalStorageに保存し、次回訪問時も維持する。
// 絞り込みで一覧(スライダーとトグルを含む領域)が差し替わるため、操作はdocumentで受けて、
// 差し替えのたびに画面の状態を保存値に合わせ直す。
(function () {
  const SIZE_KEY = "reelmilly:thumbSize";
  const FULL_KEY = "reelmilly:thumbFull";

  function load(key) {
    try {
      return localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  function save(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (err) {
      // private browsing等でlocalStorageが使えない場合は、このページを開いている間だけ有効
    }
  }

  let size = load(SIZE_KEY);
  let full = load(FULL_KEY) === "1";
  if (!size && window.matchMedia("(max-width: 640px)").matches) {
    size = "104"; // スマホの初期値: 3列くらいになる大きさ(スライダーで変えられる)
  }

  // 画面(スライダー・トグル・一覧)を、いまの値に合わせる
  function sync() {
    const slider = document.getElementById("thumb-size-slider");
    const toggle = document.getElementById("thumb-full-toggle");
    if (slider && size) slider.value = size;
    if (toggle) toggle.checked = full;
    document.querySelectorAll(".asset-grid").forEach((grid) => {
      if (size) grid.style.setProperty("--thumb-size", `${size}px`);
      grid.classList.toggle("is-full", full);
    });
  }

  document.addEventListener("input", (e) => {
    if (e.target.id === "thumb-size-slider") {
      size = e.target.value;
      save(SIZE_KEY, size);
      sync();
    }
  });

  document.addEventListener("change", (e) => {
    if (e.target.id === "thumb-full-toggle") {
      full = e.target.checked;
      save(FULL_KEY, full ? "1" : "0");
      sync();
    }
  });

  window.addEventListener("grid-updated", sync);
  sync();
})();
