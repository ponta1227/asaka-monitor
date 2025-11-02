# monitor.py（rowspan対応：直近の日付・サブ区分を継承し列マッピングで判定）
import os, re
import datetime as dt
import requests
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page
import jpholiday

HOME_URL = "https://www.pf-yoyaku.com/User/asaka/Home"

# ---- LINE ----
load_dotenv()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TO = os.getenv("LINE_TO_USER_ID")

OK_WORDS = {"○", "△", "空き", "予約可", "利用可"}
TIME_LABELS_ALL = ["午前", "午後", "夜間", "全時間帯"]

ART_DIR = Path("artifact"); ART_DIR.mkdir(parents=True, exist_ok=True)

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

# ---- Navigation ----
def navigate_to_subfacility(page: Page, sub_name: str):
    page.goto(HOME_URL, timeout=60000)
    page.get_by_text("空き照会・申込").first.click()
    page.get_by_text("一覧から探す").first.click()
    page.get_by_text("全施設").first.click()
    page.get_by_text("総合体育館（４月１日～）").first.click()
    page.get_by_text(sub_name).first.click()
    for key in ["空き状況", "空き照会", "カレンダー", "月表示", "週表示", "一覧", "月"]:
        loc = page.get_by_text(key)
        if loc.count() > 0:
            try:
                loc.first.click(); break
            except Exception: pass
    page.wait_for_load_state("domcontentloaded")

# ---- Utils ----
def _safe_text(loc): 
    try: return loc.inner_text().strip()
    except Exception: return ""
def _cells(row): 
    return row.locator("th,td")
def _any_mark(cell):
    # text
    t = _safe_text(cell)
    if t in OK_WORDS: return t
    # html
    try: html = cell.inner_html()
    except Exception: html = ""
    if html:
        for w in OK_WORDS:
            if w in html: return w
    # attributes
    for a in ["title", "aria-label", "data-label"]:
        try:
            v = cell.get_attribute(a) or ""
            if any(w in v for w in OK_WORDS): 
                for w in OK_WORDS:
                    if w in v: return w
        except Exception: pass
    # img alt
    try:
        imgs = cell.locator("img")
        for i in range(imgs.count()):
            alt = imgs.nth(i).get_attribute("alt") or ""
            for w in OK_WORDS:
                if w in alt: return w
    except Exception: pass
    # svg title
    try:
        svgt = cell.locator("svg title")
        for i in range(svgt.count()):
            vv = _safe_text(svgt.nth(i))
            for w in OK_WORDS:
                if w in vv: return w
    except Exception: pass
    return ""

def _find_time_header_index(table, wanted_labels):
    ths = table.locator("tr").nth(0).locator("th,td")
    idx = {}
    for i in range(ths.count()):
        txt = _safe_text(ths.nth(i))
        for w in wanted_labels:
            if w in txt and w not in idx:
                idx[w] = i
    return idx

def _extract_date(s, last_date):
    m = re.search(r"(\d{1,2})月\s*(\d{1,2})日", s)
    if m: return f"{m.group(1)}月{m.group(2)}日"
    return last_date

def _extract_label_hint(s, last_hint):
    # 例：メインアリーナ東1・2 / メインアリーナ東1 / メインアリーナ西 など
    m = re.search(r"(メインアリーナ[^\s　<]*)", s)
    if m: return m.group(1)
    # 他に「サブアリーナ」表記などにも対応
    m2 = re.search(r"(サブアリーナ[^\s　<]*)", s)
    if m2: return m2.group(1)
    return last_hint

def _scan_table_with_rowspan(table, wanted_labels):
    """ヘッダーで列番号を特定し、rowspan考慮で直近の日付・ラベルを継承しながら読む"""
    hits = []
    col_map = _find_time_header_index(table, wanted_labels)
    if not col_map: return hits

    rows = table.locator("tr")
    rc = rows.count()
    last_date, last_hint = None, ""

    for r in range(1, rc):  # 0 はヘッダー想定
        row = rows.nth(r)
        row_text = _safe_text(row)
        cells = _cells(row)
        cc = cells.count()

        # 行テキストから日付とラベル（サブ区分）を“更新”
        last_date = _extract_date(row_text, last_date) or last_date
        last_hint = _extract_label_hint(row_text, last_hint) or last_hint

        for label in wanted_labels:
            if label not in col_map: 
                continue
            cidx = col_map[label]
            if cidx >= cc: 
                continue
            cell = cells.nth(cidx)
            mark = _any_mark(cell)
            if mark:
                hits.append({
                    "date": last_date or "本日以降",
                    "slot": label,
                    "mark": mark,
                    "hint": last_hint or row_text[:60],
                })
    return hits

def scan_current_page(page: Page, today: dt.date):
    wanted = TIME_LABELS_ALL if is_weekend_or_holiday(today) else ["夜間"]
    all_hits = []
    # ページ本体 + すべてのiframeの<table>を対象
    frames = [page] + [f for f in page.frames if f != page.main_frame]
    for ctx in frames:
        try:
            tables = ctx.locator("table")
            for i in range(tables.count()):
                tbl = tables.nth(i)
                all_hits += _scan_table_with_rowspan(tbl, wanted)
        except Exception as e:
            print(f"[WARN] frame scan failed: {e}")
    # 重複除去
    uniq = {}
    for h in all_hits:
        k = (h["date"], h["slot"], h["mark"], h["hint"])
        uniq[k] = h
    return list(uniq.values())

def save_debug(page: Page, name: str):
    try: (ART_DIR / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e: print("save html failed:", e)
    try: page.screenshot(path=str(ART_DIR / f"{name}.png"), full_page=True)
    except Exception as e: print("save png failed:", e)

def main():
    today = dt.date.today()
    found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for sub in ["メインアリーナ", "サブアリーナ"]:
            try:
                navigate_to_subfacility(page, sub)
                page.wait_for_timeout(700)  # 軽い待ち
                hits = scan_current_page(page, today)
                for h in hits:
                    found.append((sub, h["date"], h["slot"], h["mark"], h["hint"], page.url))
                save_debug(page, f"debug_{sub}")
            except Exception as e:
                print(f"[WARN] {sub} failed: {e}")

        browser.close()

    if found:
        lines = ["【朝霞市・総合体育館 空き検知】"]
        for sub, date, slot, mark, hint, url in found:
            icon = "◎" if mark == "○" else ("⚠️" if mark == "△" else "✅")
            lines.append(f"{icon} {sub} / {date or '本日以降'} / {slot} / 表示:{mark} / {hint}\n{url}")
        send_line("\n".join(lines))
    else:
        send_line(f"ℹ️ 稼働中（空きなし）: {now_jst_str()} JST")

if __name__ == "__main__":
    main()
