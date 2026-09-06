import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import get_system_setting, set_system_setting
from engine.scanner import run_scan_and_stage

BD_TZ = timezone(timedelta(hours=6))

def get_bd_now():
    return datetime.now(BD_TZ)

def get_current_window():
    """
    Returns the adaptive scan depth and frequency based on Bangladesh Standard Time (UTC+6).
    - 08:30 AM to 11:30 AM: Morning Rush (every 20 mins, heavy depth 12)
    - 11:30 AM to 11:00 PM: Standard Daytime (every 30 mins, depth 8)
    - 11:00 PM to 08:30 AM: Overnight (every 60 mins, depth 5)
    """
    now = get_bd_now()
    hour = now.hour
    minute = now.minute
    time_val = hour + minute / 60.0

    if 8.5 <= time_val < 11.5:
        return {
            "mode_id": "morning_rush",
            "mode_name_bn": "সকালের পিক আওয়ার (Morning Rush)",
            "badge_color": "text-amber-800 bg-amber-100 border-amber-300",
            "interval_seconds": 20 * 60,
            "interval_minutes": 20,
            "limit_per_source": 12,
            "max_single_items": 8,
            "description": "সকাল ৮:৩০ - ১১:৩০: প্রতি ২০ মিনিটে ১২টি সংবাদের গভীর ও নিবিড় প্রাতঃরাশ স্ক্যান।"
        }
    elif 11.5 <= time_val < 23.0:
        return {
            "mode_id": "daytime",
            "mode_name_bn": "দিনের নিয়মিত সংবাদ প্রবাহ (Standard Daytime)",
            "badge_color": "text-blue-800 bg-blue-100 border-blue-300",
            "interval_seconds": 30 * 60,
            "interval_minutes": 30,
            "limit_per_source": 8,
            "max_single_items": 6,
            "description": "সকাল ১১:৩০ - রাত ১১:০০: প্রতি ৩০ মিনিটে ৮টি সংবাদের নিয়মিত সংবাদ স্ক্যান।"
        }
    else:
        return {
            "mode_id": "night",
            "mode_name_bn": "রাতের মেইনটেন্যান্স (Overnight)",
            "badge_color": "text-purple-800 bg-purple-100 border-purple-300",
            "interval_seconds": 60 * 60,
            "interval_minutes": 60,
            "limit_per_source": 5,
            "max_single_items": 4,
            "description": "রাত ১১:০০ - সকাল ৮:৩০: প্রতি ৬০ মিনিটে ৫টি সংবাদের শান্ত রক্ষণাবেক্ষণ স্ক্যান।"
        }

class AdaptiveScheduler:
    def __init__(self):
        self.is_scanning = False
        self.last_run_time = None
        self.next_run_time = None
        self.last_summary = None
        self.task = None

    def is_auto_scanner_enabled(self) -> bool:
        return get_system_setting("auto_scanner_enabled", "true").lower() == "true"

    def set_auto_scanner_enabled(self, enabled: bool):
        val = "true" if enabled else "false"
        set_system_setting("auto_scanner_enabled", val)
        if enabled:
            # Schedule next run shortly (within 10s if not run recently)
            window = get_current_window()
            self.next_run_time = get_bd_now() + timedelta(seconds=10)
        else:
            self.next_run_time = None

    def is_auto_publish_enabled(self) -> bool:
        return get_system_setting("auto_publish_enabled", "true").lower() == "true"

    def set_auto_publish_enabled(self, enabled: bool):
        val = "true" if enabled else "false"
        set_system_setting("auto_publish_enabled", val)

    def get_status(self) -> dict:
        window = get_current_window()
        scanner_on = self.is_auto_scanner_enabled()
        publish_on = self.is_auto_publish_enabled()

        next_seconds = None
        next_str = "নিষ্ক্রিয়"
        if scanner_on and self.next_run_time:
            now = get_bd_now()
            diff = (self.next_run_time - now).total_seconds()
            if diff > 0:
                next_seconds = int(diff)
                mins = next_seconds // 60
                secs = next_seconds % 60
                next_str = f"{mins} মিনিট {secs} সেকেন্ড পর"
            else:
                next_str = "শীঘ্রই শুরু হচ্ছে..."

        last_str = "এখনও স্বয়ংক্রিয় স্ক্যান হয়নি"
        if self.last_run_time:
            last_str = self.last_run_time.strftime("%I:%M:%S %p")

        return {
            "auto_scanner_enabled": scanner_on,
            "auto_publish_enabled": publish_on,
            "is_scanning_now": self.is_scanning,
            "window": window,
            "last_run_time_str": last_str,
            "next_run_time_str": next_str,
            "next_run_seconds": next_seconds,
            "last_summary": self.last_summary
        }

    async def execute_scheduled_scan(self):
        if self.is_scanning:
            print("[*] [Scheduler] Scan already in progress, skipping duplicate trigger.")
            return

        window = get_current_window()
        auto_publish = self.is_auto_publish_enabled()
        self.is_scanning = True
        self.last_run_time = get_bd_now()

        print(f"[*] [Scheduler] Running automated scan [{window['mode_id']} - {window['mode_name_bn']}] (Depth: {window['limit_per_source']}, Auto-Publish: {auto_publish})...")
        try:
            summary = await asyncio.to_thread(
                run_scan_and_stage,
                limit_per_source=window["limit_per_source"],
                max_single_items=window["max_single_items"],
                auto_publish=auto_publish
            )
            self.last_summary = summary
            print(f"[✓] [Scheduler] Auto-scan finished: {summary['processed_count']} stories processed (Published: {summary['published_count']}, Staged: {summary['staged_count']}).")
        except Exception as e:
            print(f"[!] [Scheduler] Scan execution error: {e}")
            self.last_summary = {"error": str(e)}
        finally:
            self.is_scanning = False
            now = get_bd_now()
            next_window = get_current_window()
            self.next_run_time = now + timedelta(seconds=next_window["interval_seconds"])

    async def run_loop(self):
        """Main background loop checking triggers every 5 seconds."""
        print("[*] [Scheduler] Background adaptive scheduler loop started.")
        if self.is_auto_scanner_enabled():
            self.next_run_time = get_bd_now() + timedelta(seconds=30)

        while True:
            try:
                await asyncio.sleep(5)
                if not self.is_auto_scanner_enabled():
                    continue

                if self.is_scanning:
                    continue

                now = get_bd_now()
                if self.next_run_time is None:
                    window = get_current_window()
                    self.next_run_time = now + timedelta(seconds=window["interval_seconds"])

                if now >= self.next_run_time:
                    await self.execute_scheduled_scan()
            except asyncio.CancelledError:
                print("[*] [Scheduler] Loop cancelled.")
                break
            except Exception as e:
                print(f"[!] [Scheduler] Loop exception: {e}")
                await asyncio.sleep(10)

# Global singleton
scheduler = AdaptiveScheduler()

