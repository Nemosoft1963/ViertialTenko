"""Japanese logistics vocabulary hints and conservative transcript normalization."""

import re

COMMON_TERMS = (
    "アルコールチェック 運転免許証 体調 睡眠 血圧 服薬 飲酒 酒気帯び "
    "日常点検 運行前点検 タイヤ 空気圧 ホイールナット ブレーキ "
    "テールランプ ヘッドライト 方向指示器 ウインカー オイル 冷却水 "
    "フロントガラス ひび 亀裂 破損 異音 警告灯 車両故障 渋滞 遅延 事故 荷崩れ 積み込み 荷下ろし "
    "配車 運行管理者 到着予定 納品 集荷 高速道路 通行止め"
)

STATUS_TERMS = {
    "awaiting_id": "車番 ナンバー ゼロ れい 一 二 三 四 五 六 七 八 九 百 千",
    "awaiting_name": "氏名 名前 フルネーム",
    "in_progress": "はい いいえ 問題ありません 大丈夫です 不調です",
    "awaiting_company_message": COMMON_TERMS,
    "awaiting_company_message_confirmation": "はい いいえ 合っています 訂正します",
}

CONSERVATIVE_CORRECTIONS = {
    "テールナンプ": "テールランプ",
    "テールラップ": "テールランプ",
    "ウインカ": "ウインカー",
    "ホイルナット": "ホイールナット",
    "日常転検": "日常点検",
    "運行前転検": "運行前点検",
}


def hotwords_for_status(status, dynamic_terms=()):
    terms = [STATUS_TERMS.get(status, COMMON_TERMS)]
    terms.extend(str(value).strip() for value in dynamic_terms if str(value).strip())
    return " ".join(terms)[:1200]


def normalize_logistics_terms(text):
    normalized = text
    corrections = []
    # Whisper often converts the vehicle-damage term "ひび" to the common
    # homophone "日々". Restrict this to windscreen context.
    contextual_corrections = (
        (r"(フロントガラス(?:に|の))日々", r"\1ひび", "フロントガラスの日々", "フロントガラスのひび"),
    )
    for pattern, replacement, source_label, target_label in contextual_corrections:
        replaced, count = re.subn(pattern, replacement, normalized)
        if count:
            normalized = replaced
            corrections.append({"from": source_label, "to": target_label})
    for source, target in CONSERVATIVE_CORRECTIONS.items():
        if source in normalized:
            normalized = normalized.replace(source, target)
            corrections.append({"from": source, "to": target})
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized, corrections
