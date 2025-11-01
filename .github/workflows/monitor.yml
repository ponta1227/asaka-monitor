# monitor.py（方法②：常に動作状況を1行送る版）
import os
import datetime as dt
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import jpholiday

HOME_URL = "https://www.pf-yoyaku.com/User/asaka/Home"

# --- LINE設定 ---
load_dotenv()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TO = os.getenv("LINE_TO_USER_ID")

def is_weekend_or_holiday(d: dt.date) -> bool:
    """土日祝なら True"""
    return d.weekday() >= 5 or jpholiday.is_holiday(d)

def send_line(text: str):
    """LINE Messaging API で Push 送信"""
    assert LINE_TOKEN and LINE_TO, "LINE credentials missing"
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": LINE_TO,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()

def navigate_to_subfacility(page, sub_name: str):
    """
    対象サブ施設（メイン/サブアリーナ）の空き画面へ移動。
    うまく動かない場合は以下で操作を録画し、出力されたセレクタでこの関数を書き換えてください：
        python -m playwright codegen https://www.pf-yoyaku.com/User/asaka/Home
    """
    page.goto(HOME_URL, timeout=60000)
    page.get_by_text("空き照会・申込").first.click()
    page.get_by_text("一覧から探す").first.click()
    page.get_by_text("全施設").first.click()
    page.get_by_text("総合体育館（４月１日～）").first.click()
    page.get_by_text(sub_name).first.click()
    for key in ["空き状況", "空き照会", "カレンダー", "月表示", "週表示"]:
        loc = page.get_by_text(key)
        if loc.count() > 0:
            try:
                loc.first.click()
                break
            except Exception:
                pass
    page.wait_for_load_state("domcontentloaded")

def scan_current_page_for_matches(page, today: dt.date):
    """
    MVPの文字列ベース検出：
    - 平日：夜間 / 週末・祝日：午前・午後・夜間・全時間帯
    - ページに「空き」または「○」があり、かつ上記ターゲット語が含まれていれば検知とみなす
    ※ DOM構造に依存しない簡易版。精密抽出は後日強化。
    """
    html = page.content()
    def contains_any(s, words): return any(w in s for w in words)

    if is_weekend_or_holiday(today):
        wanted = ["午前", "午後", "夜間", "全時間帯"]
    else:
        wanted = ["夜間"]

    if ("空き" in html or "○" in html) and contains_any(html, wanted):
        return [{"date": "本日以降", "slot": ",".join(wanted)}]
    return []

def now_jst_str(fmt="%Y-%m-%d %H:%M"):
    """JSTの現在時刻を文字列で返す（GitHub ActionsはUTC動作のため）"""
    jst = dt.datetime.utcnow() + dt.timedelta(hours=9)
    return jst.strftime(fmt)

def main():
    today = dt.date.today()
    found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for sub in ["メインアリーナ", "サブアリーナ"]:
            try:
                navigate_to_subfacility(page, sub)
                matches = scan_current_page_for_matches(page, today)
                for m in matches:
                    found.append((sub, m["date"], m["slot"], page.url))
            except Exception as e:
                # 画面遷移失敗はログだけ出して継続
                print(f"[WARN] {sub} failed: {e}")

        browser.close()

    if found:
        lines = ["【朝霞市・総合体育館 空き検知】"]
        for sub, date, slot, url in found:
            lines.append(f"・{sub} / {date} / 対象帯: {slot}\n{url}")
        send_line("\n".join(lines))
    else:
        # ← 方法②：空きが無くても“必ず”稼働メッセージを送信
        send_line(f"ℹ️ 稼働中（空きなし）: {now_jst_str()} JST")

if __name__ == "__main__":
    main()
