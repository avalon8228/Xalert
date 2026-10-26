#!/usr/bin/env python3
"""ส่งแจ้งเตือนหุ้นที่ครบ N วัน (ค่าเริ่มต้น 7) หลังวันขึ้นเครื่องหมาย X ไป Discord / LINE
ใช้เฉพาะ stdlib ไม่ต้องติดตั้งอะไรเพิ่ม

ตัวแปรสภาพแวดล้อม (ตั้งเฉพาะที่ใช้):
  DISCORD_WEBHOOK_URL   Discord webhook
  LINE_CHANNEL_TOKEN    LINE Messaging API channel access token
  LINE_USER_ID          userId (หรือ groupId) ที่จะให้ส่งไปหา

วิธีทดสอบ:  python notify.py --date 2026-10-28 --dry-run
"""
import argparse, json, os, sys, urllib.request
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

DELAY_DAYS = 7
TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def thai(d):
    return f"{d.day} {TH_MONTHS[d.month - 1]} {d.year + 543 - 2500}"


def build_message(target, events, delay):
    # เจอหุ้นที่ x_date + delay == target
    hits = OrderedDict()
    for e in events:
        x_date = date.fromisoformat(e["date"])
        if x_date + timedelta(days=delay) == target:
            hits.setdefault(x_date, OrderedDict()).setdefault(e["symbol"], []).append(e["mark"])
    if not hits:
        return None
    lines = [f"🔔 ครบ {delay} วันหลังขึ้นเครื่องหมาย X — {thai(target)}"]
    for x_date, syms in hits.items():
        lines.append(f"\nขึ้นเครื่องหมายเมื่อ {thai(x_date)}")
        for sym, marks in syms.items():
            lines.append(f"• {sym} ({', '.join(marks)})")
    return "\n".join(lines)


def post_json(url, payload, headers=None):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def chunks(text, size):
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


def send_discord(url, text):
    for part in chunks(text, 1800):  # Discord จำกัด 2000 ตัวอักษร
        post_json(url, {"content": part})


def send_line(token, user_id, text):
    for part in chunks(text, 4500):  # LINE จำกัด 5000 ตัวอักษร
        post_json("https://api.line.me/v2/bot/message/push",
                  {"to": user_id, "messages": [{"type": "text", "text": part.strip()}]},
                  {"Authorization": f"Bearer {token}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (ค่าเริ่มต้น = วันนี้ตามเวลาไทย)")
    ap.add_argument("--events", default=os.path.join(os.path.dirname(__file__), "events.json"))
    ap.add_argument("--delay", type=int, default=DELAY_DAYS)
    ap.add_argument("--dry-run", action="store_true", help="แสดงข้อความ ไม่ส่งจริง")
    a = ap.parse_args()

    today = date.fromisoformat(a.date) if a.date else datetime.now(timezone(timedelta(hours=7))).date()
    with open(a.events, encoding="utf-8") as f:
        events = json.load(f)

    msg = build_message(today, events, a.delay)
    if not msg:
        print(f"{today}: ไม่มีหุ้นที่ครบ {a.delay} วัน")
        return
    print(msg)
    if a.dry_run:
        return

    sent = False
    if os.getenv("DISCORD_WEBHOOK_URL"):
        send_discord(os.environ["DISCORD_WEBHOOK_URL"], msg)
        sent = True
    if os.getenv("LINE_CHANNEL_TOKEN") and os.getenv("LINE_USER_ID"):
        send_line(os.environ["LINE_CHANNEL_TOKEN"], os.environ["LINE_USER_ID"], msg)
        sent = True
    if not sent:
        sys.exit("ยังไม่ได้ตั้ง DISCORD_WEBHOOK_URL หรือ LINE_CHANNEL_TOKEN + LINE_USER_ID")


if __name__ == "__main__":
    main()
