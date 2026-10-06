// 一覧の絞り込みを、チェックを入れた/外した瞬間に一覧へ反映する(ページ全体は再読み込みしない)。
// - チェックボックス: すぐ反映 / 検索欄: 入力が止まってから反映
// - 一覧(#grid-region)だけを差し替え、絞り込みの選択パネルは開いたままにする
// - URLも更新するので、リロードや共有でも同じ絞り込みになる
(function () {
  const form = document.getElementById("filter-form");
  const region = document.getElementById("grid-region");
  if (!form || !region) return;

  const applyButton = document.getElementById("filter-apply");
  const clearLink = document.getElementById("filter-clear");
  if (applyButton) applyButton.hidden = true; // 即時反映するので、ボタンは不要

  let controller = null;
  let timer = null;

  function queryString() {
    const params = new URLSearchParams();
    new FormData(form).forEach((value, key) => {
      if (String(value).trim() !== "") params.append(key, value);
    });
    // 条件(すべて/いずれか)が既定のままなら、URLには載せない
    form.querySelectorAll('input[type="radio"]').forEach((r) => {
      if (r.checked && r.value === r.dataset.default) params.delete(r.name);
    });
    return params.toString();
  }

  async function apply() {
    const qs = queryString();
    const url = qs ? `/?${qs}` : "/";
    if (controller) controller.abort();
    controller = new AbortController();
    region.classList.add("is-loading");
    try {
      const res = await fetch(url, { signal: controller.signal, headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!res.ok) throw new Error(res.status);
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const fresh = doc.getElementById("grid-region");
      if (!fresh) throw new Error("grid-region not found");
      region.innerHTML = fresh.innerHTML;
      history.replaceState(null, "", url);
      if (clearLink) clearLink.hidden = qs === "";
      window.dispatchEvent(new Event("grid-updated"));
    } catch (err) {
      if (err.name === "AbortError") return; // 直後の操作で取り消された
      if (window.showToast) window.showToast("絞り込みに失敗しました。ページを再読み込みしてください", "error");
    } finally {
      region.classList.remove("is-loading");
    }
  }

  form.addEventListener("change", (e) => {
    if (e.target.matches('input[type="checkbox"], input[type="radio"]')) apply();
  });
  form.addEventListener("input", (e) => {
    if (e.target.matches('input[type="search"]')) {
      clearTimeout(timer);
      timer = setTimeout(apply, 350);
    }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault(); // Enterキーでもページ遷移せず反映する
    clearTimeout(timer);
    apply();
  });

  if (clearLink) {
    clearLink.addEventListener("click", (e) => {
      e.preventDefault();
      form.querySelectorAll('input[type="checkbox"]').forEach((cb) => { cb.checked = false; cb.indeterminate = false; });
      form.querySelectorAll('input[type="radio"]').forEach((r) => { r.checked = r.value === r.dataset.default; });
      form.querySelectorAll('input[type="search"]').forEach((i) => (i.value = ""));
      form.querySelectorAll(".ms-count").forEach((c) => { c.textContent = "0"; c.hidden = true; });
      apply();
    });
  }
})();
