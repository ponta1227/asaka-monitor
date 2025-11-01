import os
import time
import datetime as dt
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, expect
import jpholiday

HOME_URL = "https://www.pf-yoyaku.com/User/asaka/Home"

load_dotenv()

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TO = os.getenv("LINE_TO_USER_ID")

def is_weekend_or_holiday(d: dt.date) -> bool:
    return d.weekday() >= 5 or jpholiday.is_holiday(d)

def send_line(text: str):
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
    """Navigate to the availability view for a given sub-facility.
    This function intentionally uses text selectors so beginners can tweak them.
    If it fails on your machine, run:
        python -m playwright codegen https://www.pf-yoyaku.com/User/asaka/Home
    and record the clicks, then replace the selectors below.
    """
    page.goto(HOME_URL, timeout=60000)
    # "空き照会・申込"
    page.get_by_text("空き照会・申込").first.click()
    # "一覧から探す"
    page.get_by_text("一覧から探す").first.click()
    # "全施設"
    page.get_by_text("全施設").first.click()
    # Select facility: "総合体育館（４月１日～）"
    page.get_by_text("総合体育館（４月１日～）").first.click()
    # Select sub-facility (メインアリーナ or サブアリーナ)
    page.get_by_text(sub_name).first.click()
    # If there's a button like '空き状況' or 'カレンダー表示' click it
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
    """Very simple MVP parser:
    - Look for rows/sections that include our slot labels and the word '空き' or '○'
    - Try to capture the date shown near the slot (fallback to '日付不明')
    NOTE: Adjust this based on the actual DOM by printing page.content() to inspect.
    """
    html = page.content()
    matches = []

    # naive string search (works surprisingly often on text-heavy pages)
    def contains_any(s, words): return any(w in s for w in words)

    # We assume that the page shows multiple dates; to keep it simple,
    # we just report that availability exists and include the URL.
    target_slots_weekday = ["夜間"]
    target_slots_weekend = ["午前", "午後", "夜間", "全時間帯"]

    # fast path: if page mentions our subfacility and has '空き' near target keywords
    text = html

    day = today
    if is_weekend_or_holiday(day):
        wanted = target_slots_weekend
    else:
        wanted = target_slots_weekday

    if ("空き" in text or "○" in text) and contains_any(text, wanted):
        # We can't reliably parse each date/slot without DOM structure;
        # send a conservative notification.
        matches.append({
            "date": "本日以降",
            "slot": ",".join(wanted),
        })

    return matches

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
                print(f"[WARN] {sub} failed: {e}")

        browser.close()

    if found:
        lines = ["【朝霞市・総合体育館 空き検知】"]
        for sub, date, slot, url in found:
            lines.append(f"・{sub} / {date} / 対象帯: {slot}\n{url}")
        send_line("\n".join(lines))
    else:
        print("No matches.")

if __name__ == "__main__":
    main()
