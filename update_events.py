#!/usr/bin/env python3
"""ดึงปฏิทิน X จาก Settrade (สำรอง: set.or.th) มาอัปเดต events.json แล้วแจ้ง Discord เมื่อมีรายการเพิ่ม/ถูกถอด

- ดึงตั้งแต่ 7 วันก่อนวันนี้ ไปอีก ~3 เดือนข้างหน้า
- ใช้เฉพาะ stdlib; รันบนเครื่องตัวเองก็ได้ (python update_events.py)
- ถ้าดึงไม่ได้ (เช่นโดนบล็อก) จะไม่แตะข้อมูลเดิม และไม่ทำให้ workflow ล้ม
  คำเตือนเข้า Discord เฉพาะตอนกด Run workflow เอง หรือทุกวันจันทร์ (กันเตือนรัวทุกวัน)

ทดสอบ:  python update_events.py --dry-run
"""
import argparse, json, os, sys, time, urllib.error, urllib.request
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

import notify  # ใช้ thai() และ send_discord()

SOURCES = [
    {"name": "settrade",
     "url": "https://www.settrade.com/api/set/stock-calendar/{y}/{m}/x-calendar?symbols=&caTypes=",
     "referer": "https://www.settrade.com/th/equities/stock-calendar"},
    {"name": "set.or.th",
     "url": "https://www.set.or.th/api/set/stock-calendar/{y}/{m}/x-calendar?symbols=&caTypes=&lang=th",
     "referer": "https://www.set.or.th/th/market/stock-calendar/x-calendar"},
]
BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
}
LOOKBACK_DAYS = 7   # ต้องไม่น้อยกว่าจำนวนวันที่รอแจ้งเตือน (DELAY_DAYS)
MONTHS_AHEAD = 3


class FetchError(Exception):
    def __init__(self, msg, no_data=False):
        super().__init__(msg)
        self.no_data = no_data


def fetch_source(src, y, m):
    last, no_data = "unknown", False
    headers = {**BASE_HEADERS, "Referer": src["referer"]}
    for mm in dict.fromkeys([str(m), f"{m:02d}"]):  # ลองเดือนแบบไม่เติม 0 ก่อน แล้วค่อยเติม 0
        url = src["url"].format(y=y, m=mm)
        for _ in range(2):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last, no_data = f"HTTP {e.code}", e.code in (400, 404)
                if e.code in (400, 404):
                    break  # ลองรูปแบบเดือนอื่น
                if e.code == 403:
                    raise FetchError("HTTP 403 (โดนบล็อก)")
                time.sleep(3)
            except ValueError:
                last, no_data = "ข้อมูลที่ได้ไม่ใช่ JSON (อาจโดนบล็อก)", False
                time.sleep(3)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last, no_data = f"เชื่อมต่อไม่ได้ ({e})", False
                time.sleep(3)
    raise FetchError(last, no_data)


def fetch_month(y, m):
    errs = []
    for src in SOURCES:
        try:
            return fetch_source(src, y, m)
        except FetchError as e:
            errs.append((src["name"], e))
    raise FetchError(" / ".join(f"{n}: {e}" for n, e in errs), no_data=any(e.no_data for _, e in errs))


def parse(data):
    if not isinstance(data, list):
        raise FetchError("โครงข้อมูลไม่ตรงที่คาดไว้ (ไม่ใช่ list)")
    out = set()
    for day in data:
        d0 = (day.get("date") or "")[:10]
        for t in day.get("types") or []:
            for ca in t.get("corporateActions") or []:
                sym = ca.get("symbol")
                mark = ca.get("caType") or ca.get("type") or t.get("type")
                d = (ca.get("xdate") or d0)[:10]
                if sym and mark and d:
                    out.add((d, sym, mark))
    return out


def months_to_fetch(today):
    start = today - timedelta(days=LOOKBACK_DAYS)
    cur = start.year * 12 + start.month - 1
    last = today.year * 12 + today.month - 1 + MONTHS_AHEAD
    return [(c // 12, c % 12 + 1) for c in range(cur, last + 1)]


def update(today, events, fetch):
    old = {(e["date"], e["symbol"], e["mark"]) for e in events}
    start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    final, added, removed, warnings, failed = set(old), set(), set(), [], []
    for y, m in months_to_fetch(today):
        prefix = f"{y}-{m:02d}"
        try:
            new = {k for k in parse(fetch(y, m)) if k[0].startswith(prefix) and k[0] >= start}
        except Exception as e:  # noqa: BLE001
            if getattr(e, "no_data", False) and (y, m) > (today.year, today.month):
                continue  # เดือนล่วงหน้าที่ยังไม่เปิดข้อมูล ถือว่ายังไม่มี ไม่ต้องเตือน
            failed.append(f"{prefix}: {e}")
            if "403" in str(e):
                break  # โดนบล็อกแล้ว ไม่ต้องยิงต่อ
            continue
        old_m = {k for k in old if k[0].startswith(prefix) and k[0] >= start}
        gone = old_m - new
        if len(old_m) >= 10 and len(new) < len(old_m) * 0.5:
            warnings.append(f"{prefix}: ข้อมูลใหม่น้อยผิดปกติ ({len(new)} จาก {len(old_m)}) จึงไม่ลบของเดิม")
            gone = set()
        added |= new - old
        removed |= gone
        final = (final - gone) | new
        time.sleep(1)
    return final, added, removed, warnings, failed


def fmt_group(keys):
    by = OrderedDict()
    for d, s, mk in sorted(keys):
        by.setdefault(d, OrderedDict()).setdefault(mk, []).append(s)
    return [f"• {notify.thai(date.fromisoformat(d))} — " + " | ".join(f"{mk}: {', '.join(ss)}" for mk, ss in marks.items())
            for d, marks in by.items()]


def build_message(added, removed, warnings, failed):
    parts = []
    if added:
        parts += [f"🆕 ปฏิทินมีรายการเพิ่มใหม่ {len(added)} รายการ"] + fmt_group(added)
    if removed:
        parts += [f"\n🗑️ ถูกถอดออก/เปลี่ยนวัน {len(removed)} รายการ"] + fmt_group(removed)
    if warnings:
        parts += ["\n⚠️ " + w for w in warnings]
    if failed:
        parts += ["\n⚠️ ดึงปฏิทินอัตโนมัติไม่สำเร็จ (ข้อมูลเดิมยังใช้ได้ อย่าลืมอัปเดตมือ): " + "; ".join(failed)]
    return "\n".join(parts).strip() or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.json"))
    ap.add_argument("--dry-run", action="store_true", help="แสดงผล ไม่เขียนไฟล์ ไม่ส่ง Discord")
    a = ap.parse_args()

    today = datetime.now(timezone(timedelta(hours=7))).date()
    with open(a.events, encoding="utf-8") as f:
        events = json.load(f)

    final, added, removed, warnings, failed = update(today, events, fetch_month)
    print(build_message(added, removed, warnings, failed) or "ไม่มีการเปลี่ยนแปลง")

    if a.dry_run:
        return
    if added or removed:
        out = [{"date": d, "symbol": s, "mark": mk} for d, s, mk in sorted(final)]
        with open(a.events, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=0)
    # เตือนเรื่องดึงไม่สำเร็จ: เฉพาะตอนสั่งรันเอง หรือวันจันทร์ (ไม่เตือนรัวทุกวัน)
    warn_fail = os.getenv("EVENT_NAME") == "workflow_dispatch" or today.weekday() == 0
    msg = build_message(added, removed, warnings, failed if warn_fail else [])
    if msg and os.getenv("DISCORD_WEBHOOK_URL"):
        try:
            notify.send_discord(os.environ["DISCORD_WEBHOOK_URL"], msg)
        except SystemExit as e:  # ส่ง Discord พลาดไม่ควรทำให้ขั้นตอนอื่นล้ม
            print(e)


if __name__ == "__main__":
    main()
