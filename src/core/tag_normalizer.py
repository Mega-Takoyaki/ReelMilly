"""タグの整形(表記ゆれ・複合タグ・色・文のようなタグ)。

AI(特に小さなローカルモデル)が付けるタグは、「スカートとハイヒール」のような複合、「レッドビキニ」「赤いビキニ」のような色つき、
「膝を曲げて座っている」のような文、「泳池」のような中国語混じりなど、荒れやすい。そこで、保存時(と、定期の整理)に、
次の規則で整える。LLMは使わない(タグの内容を、外部に送らない)。

規則(上から順):
1. 全角/半角をそろえ、中国語の字(黑・喷・内衣など)を、日本語に直す
2. 「と」「、」「や」「＆」などでつないだ複合タグを、分ける(「・」は、名前の一部なので分けない)
3. 色つきのタグを、「色」と「物」に分ける(「レッドビキニ」→「ビキニ」「赤」)。色の標準は、赤・青・黒・白…の漢字と、ピンク・オレンジ・グレー
4. 文のようなタグ(「ハイヒールで歩き」「膝を曲げて座っている」)は、先頭の名詞だけを残す(無ければ、捨てる)
5. 別名の対応表(組み込み+画面で編集したもの)で、標準名にそろえる
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 色の標準名 → その別名(先頭に付く形)。長いものから照合する
COLORS: dict[str, list[str]] = {
    "赤": ["深紅色", "深紅", "赤色", "赤い", "赤の", "レッド", "赤"],
    "青": ["青色", "青い", "青の", "ブルー", "青"],
    "黒": ["黒色", "黒い", "黒の", "ブラック", "黒"],
    "白": ["白色", "白い", "白の", "ホワイト", "白"],
    "黄": ["黄色", "黄色い", "黄色の", "イエロー", "黄"],
    "緑": ["緑色", "緑の", "グリーン", "緑"],
    "紫": ["紫色", "紫の", "パープル", "紫"],
    "茶": ["茶色", "茶の", "ブラウン", "茶"],
    "金": ["金色", "金の", "ゴールド", "金"],
    "銀": ["銀色", "銀の", "シルバー", "銀"],
    "ピンク": ["ピンク色", "ピンクの", "桃色", "ピンク"],
    "オレンジ": ["オレンジ色", "オレンジの", "オレンジ"],
    "グレー": ["グレーの", "灰色", "グレー"],
}
COLOR_NAMES = set(COLORS)
# 色で始まるが、色ではない言葉(分けない)
COLOR_EXCEPTIONS = {
    "金髪", "黒髪", "白髪", "茶髪", "銀髪", "赤髪", "青髪", "白人", "黒人", "赤ちゃん", "白衣", "青空", "黄昏",
    "金魚", "白鳥", "ブラウス", "ブラック企業", "ホワイトデー", "ホワイトボード", "ブルーレイ", "グリーンピース",
    "ゴールデン", "シルバーウェア",
}
# 字・語の置き換え(中国語・誤字 → 日本語)
CHAR_FIXES = {"黑": "黒", "喷": "噴", "无": "", "内衣": "下着", "比基尼": "ビキニ", "游泳池": "プール", "泳池": "プール",
              "坐": "座", "牛仔": "デニム"}
# 組み込みの別名(別名 → 標準名)。画面の対応表で、足したり、上書きしたりできる
DEFAULT_ALIASES: dict[str, str] = {
    "女": "女性", "巨大乳": "巨乳", "巨大": "巨乳", "胸衣": "ブラ", "ブラジャー": "ブラ", "パンティー": "パンツ", "パンスト": "ストッキング",
    "座り": "座る", "座り姿": "座る", "座り姿勢": "座る", "座姿": "座る", "立ち上がり": "立つ", "高ヒール": "ハイヒール",
    "運動ウェア": "スポーツウェア", "フィットネスウェア": "スポーツウェア", "ネイルアート": "ネイル",
    "笑顔": "微笑む", "微笑み": "微笑む", "ブラジリアンビキニ": "ビキニ", "ブラジリアン・ビキニ": "ビキニ", "三角型ビキニ": "ビキニ",
}
# 分けない・残す(文のように見えても、標準の名前)
KEEP = {"ヴィクトリー・ポーズ", "ブラジリアン・スカート", "ブラジリアン・ビキニ", "ピース・ジェスチャー", "ビジネスイベントの会場"}

CONNECTORS = re.compile(r"(?<=[ぁ-んァ-ヴー一-龥々A-Za-z0-9])(?:と|および|及び|、|,|，|／|/|＆|&|＋|\+|や)(?=[ァ-ヴー一-龥々A-Za-z0-9])")
# 「と」「や」は、ひらがなの語の中にもあるので、前後が、カタカナ・漢字・英数のときだけ分ける
CONNECTORS_STRICT = re.compile(r"(?<=[ァ-ヴー一-龥々A-Za-z0-9])(?:と|や)(?=[ァ-ヴー一-龥々A-Za-z0-9])|(?:および|及び|、|,|，|／|/|＆|&|＋|\+)")
PARTICLE = re.compile(r"[でをにがはへ]")
JUNK = {"", "の", "と", "や", "を", "に", "が", "は", "普", "スカ", "无", "無装", "スペース"}
# 文のタグの先頭の名詞が、体の部位・曖昧な語だけのときは、捨てる
VAGUE_HEADS = {"手", "左手", "右手", "両手", "背中", "正面", "ハンド", "手の位置", "肩", "膝", "足", "胸", "股", "肢", "頭", "体", "顔", "池の周り"}
# 文のタグに含まれる姿勢・動作を、標準のタグとして取り出す(順に照合)
POSE_HINTS = [(re.compile("座"), "座る"), (re.compile("立"), "立つ"), (re.compile("横たわ|仰向"), "横たわる"),
              (re.compile("歩"), "歩く"), (re.compile("笑顔|微笑"), "微笑む"), (re.compile("泳"), "泳ぐ")]


@dataclass
class Result:
    tags: list[str] = field(default_factory=list)  # 整えたあとのタグ(空なら、捨てる)
    kinds: set[str] = field(default_factory=set)   # 何をしたか: split / color / alias / phrase / drop / fix

    @property
    def changed(self) -> bool:
        return bool(self.kinds)


def _fix_chars(text: str) -> tuple[str, bool]:
    fixed = text
    for src, dst in CHAR_FIXES.items():
        fixed = fixed.replace(src, dst)
    return fixed, fixed != text


def _split_color(part: str) -> tuple[str, str | None]:
    """先頭の色を取り出す。「レッドビキニ」→("ビキニ", "赤")。色だけのときは、("", 色)。色が無ければ、(元, None)。"""
    if part in COLOR_EXCEPTIONS or any(part.startswith(e) for e in COLOR_EXCEPTIONS if len(e) > 1):
        return part, None
    for color, names in COLORS.items():
        for name in sorted(names, key=len, reverse=True):
            if part == name:
                return "", color
            if part.startswith(name):
                rest = part[len(name):].lstrip("のな")
                if rest and rest[0] not in "ぁぃぅぇぉっゃゅょ":
                    return rest, color
    return part, None


def _head_noun(part: str) -> str:
    """文のようなタグから、先頭の名詞だけを取り出す(無ければ空)。"""
    match = PARTICLE.search(part)
    if not match:
        return part
    head = part[: match.start()]
    return head if len(head) >= 2 else ""


def is_phrase(part: str) -> bool:
    return part not in KEEP and len(part) >= 6 and bool(PARTICLE.search(part)) and not part.endswith(("会場", "背景"))


def normalize(name: str, aliases: dict[str, str] | None = None) -> Result:
    """1つのタグ名を、整える。変わらなければ、`kinds`は空で、`tags == [name]`。"""
    merged = {**DEFAULT_ALIASES, **(aliases or {})}
    result = Result()
    text = unicodedata.normalize("NFKC", name).strip().strip("「」『』\"'・ 　")
    if text != name:
        result.kinds.add("fix")
    text, fixed = _fix_chars(text)
    if fixed:
        result.kinds.add("fix")

    parts = [text] if text in KEEP else [p for p in CONNECTORS_STRICT.split(text) if p]
    if len(parts) > 1:
        result.kinds.add("split")

    out: list[str] = []
    for part in parts:
        part = part.strip("のなを ")
        if part in KEEP:
            out.append(part)
            continue
        core, color = _split_color(part)
        if color:
            result.kinds.add("color")
            out.append(color)
        if is_phrase(core):
            result.kinds.add("phrase")
            original = core
            for pattern, pose in POSE_HINTS:
                if pattern.search(original) and pose not in out:
                    out.append(pose)
            core = _head_noun(core)
            core, color2 = _split_color(core)
            if color2:
                out.append(color2)
            if core in VAGUE_HEADS:
                core = ""
        if core in merged and merged[core] != core:
            result.kinds.add("alias")
            core = merged[core]
        if core in JUNK or len(core) < 1 or (len(core) == 1 and "぀" <= core <= "ヿ"):  # 1文字のかな・カナは、捨てる
            if core:
                result.kinds.add("drop")
            continue
        out.append(core)
    seen: set[str] = set()
    result.tags = [t for t in out if not (t in seen or seen.add(t))]
    if not result.tags:
        result.kinds.add("drop")
    if result.tags == [name]:
        result.kinds.clear()
    return result
