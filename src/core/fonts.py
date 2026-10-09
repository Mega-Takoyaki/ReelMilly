"""テロップに使えるフォント(日本語対応)。Windowsに入っているものから、見つかったものだけを出す。

- 自分で足したいフォントは、状態フォルダの`fonts/`(例: `data/state/fonts/`)に、`.ttf`/`.otf`/`.ttc`を置く(再起動不要)
- 太字は、専用の太字ファイルがあれば、それを使う。無ければ、輪郭を太らせて擬似的に太くする(`core.overlay`)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from PIL import ImageFont

_WIN = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# (id, 表示名, 通常のファイル, 通常のindex, 太字のファイル, 太字のindex, 可変フォントの太さ(通常, 太字))
_BUILTIN = [
    ("yugothic", "游ゴシック（標準的な見やすさ）", "YuGothM.ttc", 0, "YuGothB.ttc", 0, None),
    ("meiryo", "メイリオ", "meiryo.ttc", 0, "meiryob.ttc", 0, None),
    ("biz-ud-gothic", "BIZ UDゴシック（読みやすい）", "BIZ-UDGothicR.ttc", 0, "BIZ-UDGothicB.ttc", 0, None),
    ("noto-sans-jp", "Noto Sans JP（細〜太）", "NotoSansJP-VF.ttf", 0, "NotoSansJP-VF.ttf", 0, (400, 800)),
    ("hg-maru-gothic", "HG丸ゴシック（やわらかい）", "HGMaruGothicMPRO_X0213(04).ttf", 0, None, 0, None),
    ("hg-pop", "HG創英角ポップ体（ポップ・目立つ）", "HGSoeiKakupoptai_X0213(04).ttc", 0, None, 0, None),
    ("hg-gothic-ub", "HG創英角ゴシックUB（極太）", "HGSoeiKakugothicUB_X0213(04).ttc", 0, None, 0, None),
    ("hg-presence", "HG創英プレゼンスEB（極太・インパクト）", "HGSoeiPresenceEB_CP932(90).ttc", 0, None, 0, None),
    ("hg-gyosho", "HG行書体（筆文字）", "HGGyoshotai_CP932(90).ttc", 0, None, 0, None),
    ("hg-kyokasho", "HG教科書体", "HGKyokashotai_CP932(90).ttc", 0, None, 0, None),
    ("yumincho", "游明朝（上品）", "yumin.ttf", 0, "yumindb.ttf", 0, None),
    ("hg-mincho-e", "HG明朝E（太い明朝）", "HGMinchoE_X0213(04).ttc", 0, None, 0, None),
    ("msgothic", "MSゴシック（等幅）", "msgothic.ttc", 0, None, 0, None),
]
DEFAULT_FONT = "yugothic"


class FontError(ValueError):
    pass


def state_dir(conn: sqlite3.Connection) -> Path:
    """DBファイルのある場所(状態フォルダ)。フォント・スタンプの置き場の基準。"""
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]).parent


def _user_fonts(directory: Path | None) -> list[tuple]:
    if directory is None or not (directory / "fonts").is_dir():
        return []
    out = []
    for p in sorted((directory / "fonts").iterdir()):
        if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
            out.append((f"user-{p.stem}", f"{p.stem}（追加したフォント）", str(p), 0, None, 0, None))
    return out


def available(directory: Path | None = None) -> list[dict]:
    """使えるフォントの一覧。各要素: id / label / bold(専用の太字があるか)。"""
    out = []
    for fid, label, regular, _i, bold, _bi, var in _BUILTIN:
        if (_WIN / regular).exists():
            out.append({"id": fid, "label": label, "bold": bool(bold and (_WIN / bold).exists()) or bool(var)})
    for fid, label, *_ in _user_fonts(directory):
        out.append({"id": fid, "label": label, "bold": False})
    return out


def load(font_id: str, size: int, bold: bool = False, directory: Path | None = None):
    """フォントを読み込む。`(フォント, 専用の太字を使ったか)`を返す。見つからなければ`FontError`。"""
    entries = {e[0]: e for e in _BUILTIN} | {e[0]: e for e in _user_fonts(directory)}
    entry = entries.get(font_id)
    if entry is None:
        raise FontError(f"不明なフォントです: {font_id}")
    _id, _label, regular, ridx, bold_file, bidx, var = entry
    base = Path(regular) if Path(regular).is_absolute() else _WIN / regular
    if not base.exists():
        raise FontError(f"フォントのファイルが見つかりません: {regular}")
    size = max(4, int(size))
    if var:  # 可変フォント: 太さを指定する
        font = ImageFont.truetype(str(base), size, index=ridx)
        font.set_variation_by_axes([var[1] if bold else var[0]])
        return font, bold
    if bold and bold_file and (_WIN / bold_file).exists():
        return ImageFont.truetype(str(_WIN / bold_file), size, index=bidx), True
    return ImageFont.truetype(str(base), size, index=ridx), False
