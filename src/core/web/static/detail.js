(function () {
  const tagList = document.getElementById("tag-list");
  const tagAddForm = document.getElementById("tag-add-form");
  const folderList = document.getElementById("folder-list");
  const folderAddForm = document.getElementById("folder-add-form");

  async function postXhr(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: new URLSearchParams(body),
    });
    if (!response.ok) {
      throw new Error(`request failed: ${response.status}`);
    }
    return response.json();
  }

  function renderTagList(tags) {
    tagList.innerHTML = "";
    tags.forEach((tag) => {
      const li = document.createElement("li");
      li.dataset.tag = tag;
      const nameSpan = document.createElement("span");
      nameSpan.className = "tag-name";
      nameSpan.textContent = tag;
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "tag-remove";
      removeBtn.dataset.tag = tag;
      removeBtn.textContent = "×";
      li.appendChild(nameSpan);
      li.appendChild(removeBtn);
      tagList.appendChild(li);
    });
    attachTagRemoveHandlers();
  }

  function renderFolderList(folders) {
    folderList.innerHTML = "";
    folders.forEach((folder) => {
      const li = document.createElement("li");
      li.dataset.folderId = folder.id;
      const nameSpan = document.createElement("span");
      nameSpan.className = "folder-name";
      nameSpan.textContent = folder.name;
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "folder-remove";
      removeBtn.dataset.folderId = folder.id;
      removeBtn.textContent = "×";
      li.appendChild(nameSpan);
      li.appendChild(removeBtn);
      folderList.appendChild(li);
    });
    attachFolderRemoveHandlers();
  }

  function attachTagRemoveHandlers() {
    tagList.querySelectorAll(".tag-remove").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const tag = btn.dataset.tag;
        const assetId = tagList.dataset.assetId;
        try {
          const data = await postXhr(
            `/assets/${assetId}/tags/${encodeURIComponent(tag)}/remove`,
            {}
          );
          renderTagList(data.tags);
        } catch (err) {
          alert("タグの削除に失敗しました");
        }
      });
    });
  }

  function attachFolderRemoveHandlers() {
    folderList.querySelectorAll(".folder-remove").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const folderId = btn.dataset.folderId;
        const assetId = folderList.dataset.assetId;
        try {
          const data = await postXhr(`/assets/${assetId}/folders/${folderId}/remove`, {});
          renderFolderList(data.folders);
        } catch (err) {
          alert("フォルダからの削除に失敗しました");
        }
      });
    });
  }

  if (tagAddForm) {
    tagAddForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = tagAddForm.querySelector("input[name=tag_name]");
      const tagName = input.value.trim();
      if (!tagName) return;
      const assetId = tagAddForm.dataset.assetId;
      try {
        const data = await postXhr(`/assets/${assetId}/tags`, { tag_name: tagName });
        renderTagList(data.tags);
        input.value = "";
      } catch (err) {
        alert("タグの追加に失敗しました");
      }
    });
  }

  if (folderAddForm) {
    folderAddForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const select = folderAddForm.querySelector("select[name=folder_id]");
      const folderId = select.value;
      if (!folderId) return;
      const assetId = folderAddForm.dataset.assetId;
      try {
        const data = await postXhr(`/assets/${assetId}/folders`, { folder_id: folderId });
        renderFolderList(data.folders);
      } catch (err) {
        alert("フォルダへの追加に失敗しました");
      }
    });
  }

  if (tagList) attachTagRemoveHandlers();
  if (folderList) attachFolderRemoveHandlers();
})();
