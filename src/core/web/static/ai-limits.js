// 設定画面「AI処理の負荷」: 一時停止・再開、使うCPUスレッド数、優先度。変更はすぐ保存され、ワーカーは次の1件から反映する。
(function () {
  const root = document.getElementById("ai-limits");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const toggle = $("ail-toggle");
  const threads = $("ail-threads");
  const low = $("ail-low");
  let timer = null;

  async function call(body) {
    const res = await fetch("/api/ai/limits", body === undefined ? undefined : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `保存できませんでした (${res.status})`);
    return data;
  }

  function render(data) {
    const { limits, status, cpu_count: cpus } = data;
    if (!threads.options.length) {
      threads.append(new Option("自動（制限しない）", "0"));
      for (const n of [1, 2, 3, 4, 6, 8].filter((x) => x < cpus)) threads.append(new Option(`${n}スレッド`, String(n)));
    }
    if (![...threads.options].some((o) => o.value === String(limits.cpu_threads))) threads.append(new Option(`${limits.cpu_threads}スレッド`, String(limits.cpu_threads)));
    threads.value = String(limits.cpu_threads);
    low.checked = limits.low_priority;
    toggle.disabled = false;
    toggle.textContent = limits.paused ? "AI処理を再開する" : "AI処理を一時停止する";
    toggle.classList.toggle("btn-primary", !limits.paused);
    const state = $("ail-state");
    const waiting = `待機中 ${status.queued}件`;
    if (limits.paused) {
      state.innerHTML = `<span class="ail-paused">一時停止中</span>（${waiting}。再開すると、続きから処理します）`;
    } else if (!status.worker_alive) {
      state.textContent = `ワーカーは停止中です（${waiting}）。reelmilly watch を起動すると処理されます。`;
    } else if (status.running) {
      state.textContent = `実行中（${waiting}）。設定を変えると、いまの1件が終わったあと、反映されます。`;
    } else {
      state.textContent = `待機中のAI処理はありません（${waiting}）。`;
    }
  }

  async function refresh() {
    try { render(await call()); } catch (err) { $("ail-state").textContent = "状態を取得できませんでした"; }
    clearTimeout(timer);
    timer = setTimeout(refresh, 8000);
  }
  async function save(body) {
    try {
      render(await call(body));
      window.showToast("保存しました（次の1件から反映されます）", "success");
    } catch (err) {
      window.showToast(err.message, "error");
    }
  }

  toggle.addEventListener("click", () => save({ paused: toggle.textContent.includes("一時停止する") }));
  threads.addEventListener("change", () => save({ cpu_threads: parseInt(threads.value, 10) }));
  low.addEventListener("change", () => save({ low_priority: low.checked }));
  refresh();
})();
