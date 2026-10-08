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
  const chipBox = document.getElementById("filter-chips");
  const modeBadges = [["broken", document.getElementById("broken-badge")], ["hidden", document.getElementById("hidden-badge")]];
  const RADIO_TEXT = { show: "含む", only: "のみ" };

  function optionText(input) {
    const label = input.closest("label");
    return label ? label.textContent.replace(/\s*\(\d+\)\s*$/, "").trim() : input.value;
  }

  // 選択中の条件を、「見出し: 値 ×」のチップで一覧の上に出す。×で、その条件だけ外せる
  function renderChips() {
    if (!chipBox) return;
    const chips = [];
    const search = form.querySelector('input[type="search"]');
    if (search && search.value.trim()) {
      chips.push({ text: `検索: ${search.value.trim()}`, remove: () => { search.value = ""; } });
    }
    // ファイルタイプ: 種別を丸ごと選んでいれば1つのチップにまとめる
    form.querySelectorAll(".tree-node").forEach((node) => {
      const parent = node.querySelector('input[name="kind"]');
      const exts = Array.from(node.querySelectorAll('input[name="ext"]'));
      const checked = exts.filter((e) => e.checked);
      if (parent.checked) {
        chips.push({ text: `ファイルタイプ: ${optionText(parent).replace(/\s*\(\d+\)$/, "")}`, input: parent });
      } else {
        checked.forEach((e) => chips.push({ text: `ファイルタイプ: ${optionText(e)}`, input: e }));
      }
    });
    form.querySelectorAll('input[type="checkbox"]:checked').forEach((input) => {
      if (input.name === "kind" || input.name === "ext") return; // 上で処理済み
      chips.push({ text: `${input.dataset.group || input.name}: ${optionText(input)}`, input });
    });
    form.querySelectorAll('input[type="radio"]:checked').forEach((r) => {
      if (r.value === r.dataset.default) return; // 既定のままの条件は出さない
      chips.push({ text: `${r.dataset.group || r.name}: ${optionText(r)}`, radio: r });
    });

    chipBox.replaceChildren(
      ...chips.map((c) => {
        const el = document.createElement("span");
        el.className = "filter-chip";
        el.append(c.text);
        const x = document.createElement("button");
        x.type = "button";
        x.setAttribute("aria-label", `${c.text} を外す`);
        x.textContent = "×";
        x.addEventListener("click", () => {
          if (c.remove) c.remove();
          if (c.input) { c.input.checked = false; c.input.dispatchEvent(new Event("change", { bubbles: true })); return; }
          if (c.radio) {
            const def = form.querySelector(`input[name="${c.radio.name}"][value="${c.radio.dataset.default}"]`);
            def.checked = true;
            def.dispatchEvent(new Event("change", { bubbles: true }));
            return;
          }
          apply();
        });
        el.appendChild(x);
        return el;
      })
    );
    chipBox.hidden = chips.length === 0;
  }

  function syncBrokenBadge() {
    modeBadges.forEach(([name, badge]) => {
      if (!badge) return;
      const mode = (form.querySelector(`input[name="${name}"]:checked`) || {}).value;
      badge.hidden = !RADIO_TEXT[mode];
      badge.textContent = RADIO_TEXT[mode] || "";
    });
  }

  function queryString() {
    const params = new URLSearchParams();
    new FormData(form).forEach((value, key) => {
      if (String(value).trim() !== "") params.append(key, value);
    });
    // 条件(すべて/いずれか)が既定のままなら、URLには載せない
    form.querySelectorAll('input[type="radio"]').forEach((r) => {
      if (r.checked && r.value === r.dataset.default) params.delete(r.name);
    });
    const sortSelect = document.querySelector('select[name="sort"][form="filter-form"]');
    if (sortSelect && sortSelect.value === sortSelect.dataset.default) params.delete("sort");
    return params.toString();
  }

  async function apply() {
    renderChips();
    syncBrokenBadge();
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
      const toolbar = document.getElementById("grid-toolbar");
      const freshToolbar = doc.getElementById("grid-toolbar");
      if (toolbar && freshToolbar) toolbar.innerHTML = freshToolbar.innerHTML;
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
  // 一覧の上のバー(並び順・フォルダ/フラット)は、form属性でこのフォームに属している(フォームの外にあるので、documentで受ける)
  document.addEventListener("change", (e) => {
    if (e.target.matches('[form="filter-form"]')) apply();
  });
  form.addEventListener("input", (e) => {
    if (e.target.matches('input[type="search"]')) {
      renderChips();
      clearTimeout(timer);
      timer = setTimeout(apply, 350);
    }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault(); // Enterキーでもページ遷移せず反映する
    clearTimeout(timer);
    apply();
  });

  renderChips(); // 読み込み時(URLで指定された絞り込み)の分

  if (clearLink) {
    clearLink.addEventListener("click", (e) => {
      e.preventDefault();
      form.querySelectorAll('input[type="checkbox"]').forEach((cb) => { cb.checked = false; cb.indeterminate = false; });
      form.querySelectorAll('input[type="radio"]').forEach((r) => { r.checked = r.value === r.dataset.default; });
      form.querySelectorAll(".tree-node input").forEach((cb) => { cb.indeterminate = false; });
      form.querySelectorAll('input[type="search"]').forEach((i) => (i.value = ""));
      form.querySelectorAll(".ms-count").forEach((c) => { c.textContent = "0"; c.hidden = true; });
      apply();
    });
  }
})();
