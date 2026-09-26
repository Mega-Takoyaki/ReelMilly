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
