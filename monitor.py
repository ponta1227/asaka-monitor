import os
import re
import datetime as dt
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError
import jpholiday

HOME_URL = "https://www.pf-yoyaku.com/User/asaka/Home"

# --- LINE 設定 ---
load_dotenv()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TO = os.getenv("LINE_TO_USER_ID")

MARK_OK = {"○", "△", "空き"}  # 検出対象
TIME_LABELS_ALL = ["午前", "午後", "夜間", "全時間帯"]

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
def navigate_to_subfacility(page, sub_name: str):
    """
    サイト階層を辿って対象サブ施設（メイン/サブアリーナ）の空き表示に到達。
    もしクリック箇所が変わっていたら、codegenで録画して置き換え：
        python -m playwright codegen https://www.pf-yoyaku.com/User/asaka/Home
    """
    page.goto(HOME_URL, timeout=60000)
    page.get_by_text("空き照会・申込").first.click()
    page.get_by_text("一覧から探す").first.click()
    page.get_by_text("全施設").first.click()
    page.get_by_text("総合体育館（４月１日～）").first.click()
    page.get_by_text(sub_name).first.click()

    # 表示切替（存在するキーのどれか）
    for key in ["空き状況", "空き照会", "カレンダー", "月表示", "週表示", "一覧"]:
        loc = page.get_by_text(key)
        if loc.count() > 0:
            try:
                loc.first.click()
                break
            except Exception:
                pass

    page.wait_for_load_state("domcontentloaded")

# -------- DOM 解析 --------
def _all_frames(page):
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
    テーブルの先頭行から、指定ラベル（夜間/午前/午後/全時間帯）の列インデックスを辞書で返す。
    複数行ヘッダーでも、最初に見つかった行で決めるシンプル方式。
    """
    header_rows = table.locator("tr").nth(0)
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

def _safe_inner_text(locator):
    try:
        return locator.inner_text().strip()
    except Exception:
        return ""

def _find_matches_in_table(table, wanted_labels):
    """
    1つの<table>について、ヘッダーから列位置を見つけ、
    各行の対象セルに「○/△/空き」があるかを調べてヒットを返す。
    """
    matches = []
    # ヘッダーの列インデックスを確定
    col_map = _header_col_index(table, wanted_labels)
    if not col_map:
        return matches  # 夜間/午前/午後等が見つからない表はスキップ

    # 行をループ（1行目はヘッダーとみなして2行目以降）
    rows = table.locator("tr")
    row_count = rows.count()
    last_date = None

    for r in range(1, row_count):
        row = rows.nth(r)
        row_text = _safe_inner_text(row)
        date_text = _extract_date_from_row(row_text, last_date)
        last_date = date_text

        # サブコート名など（例：メインアリーナ東1・2）を行テキストから拾っておく
        # なければ空文字（通知時に行テキストそのものを出すほうが分かりやすい場合も）
        label_hint = ""
        m2 = re.search(r"(メインアリーナ[^\s　]*)", row_text)
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
            cell_text = _safe_inner_text(cells.nth(cidx))
            if cell_text in MARK_OK:
                matches.append({
                    "date": date_text,
                    "slot": key,
                    "mark": cell_text,
                    "hint": label_hint or row_text[:50]  # 行の雰囲気を少し通知に載せる
                })
    return matches

def scan_current_page_for_matches(page, today: dt.date):
    """
    - 平日：夜間のみ
    - 土日祝：午前・午後・夜間・全時間帯
    ページ本体およびiframe内の全<table>を調べ、ヘッダーから列位置を決めて検出。
    """
    wanted = TIME_LABELS_ALL if is_weekend_or_holiday(today) else ["夜間"]
    hits = []

    # ページ本体 + すべての iframe を対象にスキャン
    contexts = _all_frames(page)
    for ctx in contexts:
        try:
            # 夜間などのラベルを含むテーブルを優先的に探す
            tables = ctx.locator("table:has(th:has-text('夜間')), table:has(td:has-text('夜間'))")
            tcount = tables.count()
            if tcount == 0:
                # フォールバック：全テーブル
                tables = ctx.locator("table")
                tcount = tables.count()

            for i in range(tcount):
                tbl = tables.nth(i)
                try:
                    hits.extend(_find_matches_in_table(tbl, wanted))
                except Exception as inner:
                    print(f"[WARN] table {i} parse failed: {inner}")
        except Exception as e:
            print(f"[WARN] frame scan failed: {e}")

    # 同一（日付×時間帯×記号）の重複を削除
    uniq = {}
    for h in hits:
        k = (h["date"], h["slot"], h["mark"], h["hint"])
        uniq[k] = h
    return list(uniq.values())

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
                # 待機（動的ロード対策）
                try:
                    page.wait_for_timeout(800)  # 軽い待ち
                except TimeoutError:
                    pass

                matches = scan_current_page_for_matches(page, today)
                for m in matches:
                    found.append((sub, m["date"], m["slot"], m["mark"], m["hint"], page.url))
            except Exception as e:
                print(f"[WARN] navigate/scan failed for {sub}: {e}")

        browser.close()

    if found:
        lines = ["【朝霞市・総合体育館 空き検知】"]
        for sub, date, slot, mark, hint, url in found:
            # 表示マークで少し強調
            icon = "◎" if mark == "○" else ("⚠️" if mark == "△" else "✅")
            lines.append(f"{icon} {sub} / {date} / {slot} / 表示:{mark} / {hint}\n{url}")
        send_line("\n".join(lines))
    else:
        # 常時通知（稼働確認）
        send_line(f"ℹ️ 稼働中（空きなし）: {now_jst_str()} JST")

if __name__ == "__main__":
    main()
