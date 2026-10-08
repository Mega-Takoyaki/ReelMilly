def edit(p, pairs):
    with open(p, encoding="utf-8", newline="") as f:
        s = f.read()
    nl = "\r\n" if "\r\n" in s else "\n"
    s = s.replace("\r\n", "\n")
    for a, b in pairs:
        assert a in s, (p, a[:60])
        s = s.replace(a, b, 1)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace("\n", nl))


edit("src/core/web/templates/help.html", [
    ('''    <li>「投稿する」を押すと確認が出て''', '''    <li><strong>予約投稿</strong>: 「投稿のタイミング」で<strong>「予約する」</strong>を選ぶと、日時を決めて予約できます（Fanvue・X のどちらも）。FanvueやXの予約機能は使わず、<strong>ReelMilly が時刻を管理</strong>して、時間になると <code>reelmilly watch</code> が自動で投稿します。予約するときにも検査します（未連携・Xの制限など）。時刻に <code>watch</code> が止まっていたときは、再開後に、遅れて実行します。予約は、設定の「投稿スケジュール」タブの「予約中の投稿」で、確認・<strong>取り消し</strong>・<strong>いますぐ実行</strong>ができます。実行に失敗したときは、再試行せずに「失敗」として残り、通知に出ます（二重投稿を避けるため。予約し直してください）。同じ作品を同じ投稿先へ予約済みのときは、ダイアログに知らせます。</li>
    <li>「投稿する」を押すと確認が出て'''),
])
edit("README.md", [(
    "### Xへの投稿",
    """### 予約投稿

「今すぐ投稿」のダイアログで「予約する」を選ぶと、日時を決めて予約できます（Fanvue・X）。先方の予約機能は使わず、予約の内容を`scheduled_posts`テーブルに保存して、時刻になったら`reelmilly watch`の予約実行用スレッド（10秒おきに確認）が、その時点の状態で、もう一度検査して投稿します。失敗しても再試行せず（二重投稿を避ける）、失敗として残して通知します。`watch`が止まっていた間に過ぎた予約は、再開後に遅れて実行します。確認・取り消し・いますぐ実行は、設定の「投稿スケジュール」タブで行います。

### Xへの投稿"""),
])
edit("TODO.md", [(
    "- [ ] 今すぐ投稿のX対応",
    """- [x] 予約投稿(Fanvue・X): 「今すぐ投稿」で日時を決めて予約。ReelMilly(watchの予約実行用スレッド)が時刻に投稿。設定の「投稿スケジュール」で、確認・取り消し・いますぐ実行
- [ ] 予約投稿の続き: 予約内容の編集(いまは、取り消して予約し直す)、詳細画面での予約の表示、繰り返しの予約、遅れすぎた予約の扱い(いまは、いつでも実行する)
- [ ] 今すぐ投稿のX対応"""),
])
print("ok")
