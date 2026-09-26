(function () {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("dropzone-file-input");
  const statusEl = document.getElementById("dropzone-status");

  if (!dropzone || !fileInput) return;

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  async function uploadFiles(fileList) {
    if (!fileList || fileList.length === 0) return;

    const formData = new FormData();
    for (const file of fileList) {
      formData.append("files", file);
    }

    dropzone.classList.add("uploading");
    setStatus(`アップロード中... (${fileList.length}件)`);

    try {
      const response = await fetch("/assets/upload", {
        method: "POST",
        body: formData,
      });
      const data = await response.json();

      if (!response.ok) {
        setStatus(`エラー: ${data.error || "アップロードに失敗しました"}`);
        return;
      }

      let message = `${data.ingested}件を取り込みました`;
      if (data.rejected && data.rejected.length > 0) {
        message += `（非対応形式のためスキップ: ${data.rejected.join(", ")}）`;
      }
      setStatus(message);
      setTimeout(() => window.location.reload(), 800);
    } catch (err) {
      setStatus("アップロード中にエラーが発生しました");
      console.error(err);
    } finally {
      dropzone.classList.remove("uploading");
    }
  }

  dropzone.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    uploadFiles(fileInput.files);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const files = e.dataTransfer ? e.dataTransfer.files : null;
    uploadFiles(files);
  });
})();
