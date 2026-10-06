// 一覧画面: 画像・動画のアップロード。
// - 「画像アップロード」ボタンからファイルを選ぶ
// - サムネイル表示エリア全体へのドラッグ&ドロップ
// 進捗は "upload-progress" イベントで、ジョブ状況の表示(ai-live.js)へ伝える。
(function () {
  const area = document.getElementById("dropzone");
  const button = document.getElementById("upload-button");
  const fileInput = document.getElementById("upload-input");

  if (!area || !button || !fileInput) return;

  function emit(detail) {
    window.dispatchEvent(new CustomEvent("upload-progress", { detail }));
  }

  let uploading = false;

  function uploadFiles(fileList) {
    if (uploading || !fileList || fileList.length === 0) return;
    const files = Array.from(fileList);

    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));

    uploading = true;
    button.disabled = true;
    area.classList.add("uploading");
    emit({ active: true, count: files.length, percent: 0 });

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/assets/upload");
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) emit({ active: true, count: files.length, percent: Math.round((e.loaded / e.total) * 100) });
    });
    xhr.addEventListener("load", () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* 下でエラー扱い */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        let message = `${data.ingested}件をアップロードしました`;
        if (data.rejected && data.rejected.length > 0) {
          message += `（非対応形式のためスキップ: ${data.rejected.join(", ")}）`;
        }
        if (window.showToast) window.showToast(message, "success");
        setTimeout(() => window.location.reload(), 800);
      } else if (window.showToast) {
        window.showToast(`アップロードに失敗しました: ${data.error || xhr.status}`, "error");
      }
    });
    xhr.addEventListener("error", () => {
      if (window.showToast) window.showToast("アップロード中にエラーが発生しました", "error");
    });
    xhr.addEventListener("loadend", () => {
      uploading = false;
      button.disabled = false;
      area.classList.remove("uploading");
      emit({ active: false });
    });
    xhr.send(formData);
  }

  button.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    uploadFiles(fileInput.files);
    fileInput.value = "";
  });

  // サムネイル表示エリア全体がドロップ先。子要素をまたぐdragenter/leaveのちらつきを数で吸収する
  function hasFiles(e) {
    return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
  }
  let depth = 0;
  area.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth += 1;
    area.classList.add("dragover");
  });
  area.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  area.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0) area.classList.remove("dragover");
  });
  area.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    area.classList.remove("dragover");
    uploadFiles(e.dataTransfer.files);
  });

  // エリアの外にドロップしたときに、ブラウザがファイルを開いて画面遷移してしまうのを防ぐ
  ["dragover", "drop"].forEach((name) => {
    window.addEventListener(name, (e) => {
      if (hasFiles(e) && !area.contains(e.target)) e.preventDefault();
    });
  });
})();
