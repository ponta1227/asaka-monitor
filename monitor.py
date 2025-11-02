# monitor.py（総合版：○/△/空き/予約可/利用可 判定 + 未来巡回 + デバッグ成果物 + 常時通知）
import os
import re
import datetime as dt
import requests
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError, Page
import jpholiday

HOME_URL = "https://www.pf-yoyaku.com/User/asaka/Home"

# --- LINE 設定 ---
load_dotenv()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TO = os.getenv("LINE_TO_USER_ID")

# 検出マーク（文字 or 属性）
MARK_OK_TEXTS = {"○", "△", "空き", "予約可", "利用可"}

# スキャンする時間帯ラベル
TIME_LABELS_ALL = ["午前", "午後", "夜間", "全時間帯"]

# ページ送りで試すボタン
NEXT_BUTTON_CANDIDATES = ["翌月", "次へ", "次", "＞", ">>", "▶", "▶︎", "▶️"]

# アーティファクト出力先
ART_DIR = Path("artifact")
ART_DIR.mkdir(exist_ok=True, parents=True)


def is_weekend_or_holiday(d: dt.date) -> bool:
    return d.weekday() >= 5 or jpholiday.is_holiday(d)


def send_line(text: str):
    assert LINE_TOKEN and LINE_TO, "LINE credentials missing"
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_TO, "messages": [{"type": "text", "text": text[:4900]}]}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()


def now_jst_str(fmt="%Y-%m-%d %H:%M"):
    jst = dt.datetime.utcnow() + dt.timedelta(hours=9)
    return jst.strftime(fmt)


# -------- 画面遷移 --------
def navigate_to_subfacility(page: Page, sub_name: str):
    """
    対象サブ施設（メイン/サブアリーナ）の空き表示に到達。
    うまく動かない場合は、codegenで録画して置き換え：
        python -m playwright codegen https://www.pf-yoyaku.com/User/asaka/Home
    """
    page.goto(HOME_URL, timeout=60000)
    page.get_by_text("空き照会・申込").first.click()
    page.get_by_text("一覧から探す").first.click()
    page.get_by_text("全施設").first.click()
    page.get_by_text("総合体育館（４月１日～）").first.click()
    page.get_by_text(sub_name).first.click()

    # 表示切替（存在するキーのどれか）
    for key in ["空き状況", "空き照会", "カレンダー", "月表示", "週表示", "一覧", "月"]:
        loc = page.get_by_text(key)
        if loc.count() > 0:
            try:
                loc.first.click()
                break
            except Exception:
                pass

    page.wait_for_load_state("domcontentloaded")


# -------- DOMユーティリティ --------
def _all_frames(page: Page):
    """ページ本体 + すべてのiframeのFrameオブジェクトを列挙"""
    frames = [page]
    try:
        for fr in page.frames:
            if fr != page.main_frame:
                frames.append(fr)
    except Exception:
        pass
    return frames


def _header_col_index(table, label_keywords):
    """
    テーブルの1行目から、指定ラベル（夜間/午前/午後/全時間帯）の列インデックスを辞書で返す。
    """
    ths = table.locator("tr").nth(0).locator("th,td")
    count = ths.count()
    idx_map = {}
    for i in range(count):
        t = ths.nth(i).inner_text().strip()
        for key in label_keywords:
            if key in t and key not in idx_map:
                idx_map[key] = i
    return idx_map


def _extract_date_from_row(row_text, last_date):
    """
    行テキストから '12月12日' のような日付を拾う（見つからなければ前回値を継承）
    """
    m = re.search(r"(\d{1,2})月\s*(\d{1,2})日", row_text)
    if m:
        return f"{m.group(1)}月{m.group(2)}日"
    return last_date or "本日以降"


def _row_cells(table, row_idx):
    return table.locator("tr").nth(row_idx).locator("th,td")


def _safe_text(locator):
    try:
        return locator.inner_text().strip()
    except Exception:
        return ""


def _any_mark_in_cell(cell_locator) -> tuple[bool, str]:
    """
    1セルからあらゆる情報源（text/html/aria/title/img alt/svg titleなど）を確認して
    検出マークがあれば (True, "○"等) を返す。
    """
    # 1) テキスト
    try:
        text = cell_locator.inner_text().strip()
        if text in MARK_OK_TEXTS:
            return True, text
    except Exception:
        text = ""

    # 2) HTML
    try:
        html = cell_locator.inner_html().lower()
    except Exception:
        html = ""

    # 3) 属性
    candidates = []
    for attr in ["title", "aria-label", "data-label"]:
        try:
            val = cell_locator.get_attribute(attr)
            if val:
                candidates.append(val)
        except Exception:
            pass

    # 4) img の alt
    try:
        imgs = cell_locator.locator("img")
        for i in range(imgs.count()):
            alt = imgs.nth(i).get_attribute("alt")
            if alt:
                candidates.append(alt)
    except Exception:
        pass

    # 5) svg 内の title 要素
    try:
        svgt = cell_locator.locator("svg title")
        for i in range(svgt.count()):
            t = svgt.nth(i).inner_text()
            if t:
                candidates.append(t)
    except Exception:
        pass

    # HTMLの生文字列からもワードを拾う
    if html:
        for w in MARK_OK_TEXTS:
            if w in html:
                return True, w

    # 候補属性を精査
    for c in candidates:
        for w in MARK_OK_TEXTS:
            if w in c:
                return True, w

    return False, ""


def _find_matches_in_table(table, wanted_labels):
    """
    1つの<table>について、ヘッダーから列位置を見つけ、
    各行の対象セルに○/△/空き/予約可/利用可があるかを調べてヒットを返す。
    """
    matches = []
    col_map = _header_col_index(table, wanted_labels)
    if not col_map:
        return matches

    rows = table.locator("tr")
    row_count = rows.count()
    last_date = None

    for r in range(1, row_count):  # 0行目はヘッダー
        row = rows.nth(r)
        row_text = _safe_text(row)
        date_text = _extract_date_from_row(row_text, last_date)
        last_date = date_text

        # サブ区分のヒント（例：メインアリーナ東1・2）
        label_hint = ""
        m2 = re.search(r"(メインアリーナ[^\s　<]*)", row_text)
        if m2:
            label_hint = m2.group(1)

        cells = _row_cells(table, r)
        cell_count = cells.count()

        for key in wanted_labels:
            if key not in col_map:
                continue
            cidx = col_map[key]
            if cidx >= cell_count:
                continue

            cell = cells.nth(cidx)
            ok, mark = _any_mark_in_cell(cell)
            if ok:
                matches.append({
                    "date": date_text,
                    "slot": key,
                    "mark": mark,
                    "hint": label_hint or row_text[:60]
                })
    return matches


def scan_page_across_future(page: Page, today: dt.date, max_steps: int = 8):
    """
    今日以降を広めに探索するため、最大 max_steps 回まで「次へ/翌月」等でページ送りしながら
    各ページのテーブルを解析してヒットを返す。
    """
    wanted = TIME_LABELS_ALL if is_weekend_or_holiday(today) else ["夜間"]
    hits = []

    def scan_once():
        contexts = _all_frames(page)
        local_hits = []
        for ctx in contexts:
            try:
                tables = ctx.locator("table")
                tcount = tables.count()
                for i in range(tcount):
                    tbl = tables.nth(i)
                    try:
                        local_hits.extend(_find_matches_in_table(tbl, wanted))
                    except Exception as inner:
                        print(f"[WARN] table parse failed: {inner}")
            except Exception as e:
                print(f"[WARN] frame scan failed: {e}")
        return local_hits

    # 現在ページをスキャン
    hits.extend(scan_once())

    # 未来にページ送りしながらスキャン
    for step in range(max_steps):
        moved = False
        for label in NEXT_BUTTON_CANDIDATES:
            btn = page.get_by_text(label, exact=False)
            if btn.count() > 0:
                try:
                    btn.first.click()
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(600)
                    moved = True
                    break
                except Exception:
                    pass
        if not moved:
            break
        hits.extend(scan_once())

    # 重複排除
    uniq = {}
    for h in hits:
        k = (h["date"], h["slot"], h["mark"], h["hint"])
        uniq[k] = h
    return list(uniq.values())


def save_debug_artifacts(page: Page, prefix: str):
    """
    画面のHTMLとスクリーンショットを artifact/ に保存（Actionsでダウンロード可）
    """
    try:
        (ART_DIR / f"{prefix}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] save html failed: {e}")
    try:
        page.screenshot(path=str(ART_DIR / f"{prefix}.png"), full_page=True)
    except Exception as e:
        print(f"[WARN] save screenshot failed: {e}")


# -------- メイン --------
def main():
    today = dt.date.today()
    found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for sub in ["メインアリーナ", "サブアリーナ"]:
            try:
                navigate_to_subfacility(page, sub)
                page.wait_for_timeout(800)  # 軽い待ち（動的ロード対策）

                matches = scan_page_across_future(page, today, max_steps=8)
                for m in matches:
                    found.append((sub, m["date"], m["slot"], m["mark"], m["hint"], page.url))

                # デバッグ成果物（最新ページ）を保存
                save_debug_artifacts(page, f"debug_{sub}")

            except Exception as e:
                print(f"[WARN] navigate/scan failed for {sub}: {e}")

        browser.close()

    if found:
        lines = ["【朝霞市・総合体育館 空き検知】"]
        for sub, date, slot, mark, hint, url in found:
            icon = "◎" if mark == "○" else ("⚠️" if mark == "△" else "✅")
            lines.append(f"{icon} {sub} / {date} / {slot} / 表示:{mark} / {hint}\n{url}")
        send_line("\n".join(lines))
    else:
        # 常時通知（稼働確認）
        send_line(f"ℹ️ 稼働中（空きなし）: {now_jst_str()} JST")


if __name__ == "__main__":
    main()
