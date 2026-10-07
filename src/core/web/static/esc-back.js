// 詳細画面: Escキーで一覧画面に戻る(入力中・ダイアログ表示中は何もしない)。
(function () {
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    if (document.querySelector("dialog[open]")) return;
    const menu = document.querySelector("details.menu[open]");
    if (menu) {
      menu.open = false; // 画面を戻る前に、開いているメニューを閉じる
      return;
    }
    const el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) {
      el.blur(); // 入力欄ではまずフォーカスを外すだけ(誤って画面遷移しない)
      return;
    }
    // 一覧から来た場合は戻る(絞り込み条件・スクロール位置を保てる)。直接開いた場合は一覧へ移動
    const fromList = document.referrer && new URL(document.referrer).origin === location.origin;
    if (fromList && history.length > 1) history.back();
    else location.href = "/";
  });
})();
