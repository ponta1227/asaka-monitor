# monitor.py（最終ブースト版：表解析 + 属性/画像 + 近傍テキスト検索 + デバッグ送信）
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

# 任意指定（あるなら優先検索）
FORCE_DATE = os.getenv("FORCE_DATE")  # 例: "12月12日"
FORCE_KEYWORDS = os.getenv("FORCE_KEYWORDS", "メインアリーナ東1・2,メインアリーナ東1,メインアリーナ東2")
FORCE_SPAN = int(os.getenv("FORCE_SPAN", "320"))  # 近傍幅

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
    page.wait_for_timeout(800)
    # 画面全体に要素を出し切る（遅延ロード対策）
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
    except Exception:
        pass

# ---- Utils ----
def _safe_text(loc):
    try: return loc.inner_text().strip()
    except Exception: return ""

def _any_mark(cell):
    t = _safe_text(cell)
    if t in OK_WORDS: return t
    try:
        html = cell.inner_html()
        if html and any(w in html for w in OK_WORDS):
            for w in OK_WORDS:
                if w in html: return w
    except Exception: pass
    for a in ["title", "aria-label", "data-label"]:
        try:
            v = cell.get_attribute(a) or ""
            for w in OK_WORDS:
                if w in v: return w
        except Exception: pass
    try:
        imgs = cell.locator("img")
        for i in range(imgs.count()):
            alt = imgs.nth(i).get_attribute("alt") or ""
            for w in OK_WORDS:
                if w in alt: return w
    except Exception: pass
    try:
        svgt = cell.locator("svg title")
        for i in range(svgt.count()):
            vv = _safe_text(svgt.nth(i))
            for w in OK_WORDS:
                if w in vv: return w
    except Exception: pass
    return ""

def _time_header_index(table, labels):
    ths = table.locator("tr").nth(0).locator("th,td")
    idx = {}
    for i in range(ths.count()):
        txt = _safe_text(ths.nth(i))
        for w in labels:
            if w in txt and w not in idx:
                idx[w] = i
    return idx

def _extract_date(s, last_date):
    m = re.search(r"(\d{1,2})月\s*(\d{1,2})日", s)
    return f"{m.group(1)}月{m.group(2)}日" if m else last_date

def _extract_row_label(s, last_hint):
    for pat in [r"(メインアリーナ[^\s　<]*)", r"(サブアリーナ[^\s　<]*)"]:
        m = re.search(pat, s)
        if m: return m.group(1)
    return last_hint

def _scan_table(table, wanted_labels):
    hits = []
    col_map = _time_header_index(table, wanted_labels)
    if not col_map: return hits
    rows = table.locator("tr"); rc = rows.count()
    last_date, last_hint = None, ""
    for r in range(1, rc):
        row = rows.nth(r)
        row_text = _safe_text(row)
        cells = row.locator("th,td")
        cc = cells.count()
        last_date = _extract_date(row_text, last_date) or last_date
        last_hint = _extract_row_label(row_text, last_hint) or last_hint
        for label in wanted_labels:
            if label not in col_map: continue
            cidx = col_map[label]
            if cidx >= cc: continue
            mark = _any_mark(cells.nth(cidx))
            if mark:
                hits.append({
                    "date": last_date or "本日以降",
                    "slot": label,
                    "mark": mark,
                    "hint": last_hint or row_text[:60]
                })
    return hits

def _frames(page: Page):
    return [page] + [f for f in page.frames if f != page.main_frame]

def _scan_dom(page: Page, today: dt.date):
    wanted = TIME_LABELS_ALL if is_weekend_or_holiday(today) else ["夜間"]
    hits = []
    for ctx in _frames(page):
        try:
            tables = ctx.locator("table")
            for i in range(tables.count()):
                hits += _scan_table(tables.nth(i), wanted)
        except Exception as e:
            print("[WARN] frame scan failed:", e)
    # uniq
    uniq = {}
    for h in hits:
        k = (h["date"], h["slot"], h["mark"], h["hint"])
        uniq[k] = h
    return list(uniq.values())

def _plain_text(page: Page) -> str:
    # ページ全体の可視テキスト
    try:
        return page.locator("body").inner_text()
    except Exception:
        return page.content()

def _nearby_text_hits(page: Page, today: dt.date):
    """
    表解析で拾えない場合の非常口。
    可視テキストから「(FORCE_DATE or 任意日付候補) + 夜間 + OK_WORDS + キーワード群」の近傍一致で検出。
    """
    text = _plain_text(page)
    text_norm = re.sub(r"\s+", " ", text)
    wanted_labels = TIME_LABELS_ALL if is_weekend_or_holiday(today) else ["夜間"]
    date_patterns = []
    if FORCE_DATE:
        date_patterns = [FORCE_DATE]
    else:
        # きょう以降20日分くらいの "M月D日" を作って候補に（念のため）
        base = today
        for i in range(0, 60):
            d = base + dt.timedelta(days=i)
            date_patterns.append(f"{d.month}月{d.day}日")
    keywords = [s.strip() for s in FORCE_KEYWORDS.split(",") if s.strip()]

    hits = []
    for kw in keywords:
        for date_str in date_patterns:
            for label in wanted_labels:
                # 近傍：date ... label ... OK_WORDS ... kw の任意順序を緩くチェック
                # まずキーワード位置を軸に前後 FORCE_SPAN 文字を切り出し
                for m in re.finditer(re.escape(kw), text_norm):
                    start = max(0, m.start() - FORCE_SPAN)
                    end = min(len(text_norm), m.end() + FORCE_SPAN)
                    window = text_norm[start:end]
                    if (date_str in window) and (label in window) and any(w in window for w in OK_WORDS):
                        mark = next((w for w in ["○","△","空き","予約可","利用可"] if w in window), "空き")
                        hits.append({
                            "date": date_str,
                            "slot": label,
                            "mark": mark,
                            "hint": kw,
                            "debug": window[:300]  # LINEに抜粋も送る
                        })
    # uniq
    uniq = {}
    for h in hits:
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
                # 1) DOMベース（表解析）
                hits1 = _scan_dom(page, today)
                # 2) 非常口：近傍テキスト検索（例：東1・2 対応）
                hits2 = _nearby_text_hits(page, today)
                for h in (hits1 + hits2):
                    found.append((sub, h.get("date") or "本日以降", h["slot"], h["mark"], h["hint"], page.url, h.get("debug","")))
                save_debug(page, f"debug_{sub}")
            except Exception as e:
                print(f"[WARN] {sub} failed: {e}")

        browser.close()

    if found:
        lines = ["【朝霞市・総合体育館 空き検知】"]
        for sub, date, slot, mark, hint, url, dbg in found:
            icon = "◎" if mark == "○" else ("⚠️" if mark == "△" else "✅")
            msg = f"{icon} {sub} / {date} / {slot} / 表示:{mark} / {hint}\n{url}"
            # 近傍テキストの抜粋も添付（あれば）
            if dbg:
                msg += f"\n── 抜粋 ──\n{dbg}"
            lines.append(msg)
        send_line("\n".join(lines))
    else:
        send_line(f"ℹ️ 稼働中（空きなし）: {now_jst_str()} JST")

if __name__ == "__main__":
    main()
