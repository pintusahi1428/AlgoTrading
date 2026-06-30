from pathlib import Path

p = Path("token_manager.py")
txt = p.read_text(encoding="utf-8")

if "def get_current_expiry(" in txt:
    print("get_current_expiry already exists.")
else:
    patch = r'''

# ===== EXPIRY HELPER - CMD FIX =====
def get_current_expiry(index_name):
    import datetime

    today = datetime.date.today()
    index_name = str(index_name).upper()

    if index_name == "NIFTY":
        days_ahead = (1 - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = today + datetime.timedelta(days=days_ahead)
    elif index_name == "BANKNIFTY":
        year = today.year
        month = today.month
        next_month = today.replace(day=28) + datetime.timedelta(days=4)
        last_day = next_month - datetime.timedelta(days=next_month.day)
        offset = (last_day.weekday() - 2) % 7
        target_date = last_day - datetime.timedelta(days=offset)
        if today > target_date:
            month = month + 1 if month < 12 else 1
            year = year if month > 1 else year + 1
            if month == 12:
                last_day = datetime.date(year, 12, 31)
            else:
                last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
            offset = (last_day.weekday() - 2) % 7
            target_date = last_day - datetime.timedelta(days=offset)
    else:
        days_ahead = (3 - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = today + datetime.timedelta(days=days_ahead)

    day = target_date.strftime("%d").lstrip("0")
    month = target_date.strftime("%b").upper()
    year = target_date.strftime("%y")
    return f"{day}{month}{year}"
# ===== END EXPIRY HELPER =====
'''
    p.write_text(txt + "\n" + patch + "\n", encoding="utf-8")
    print("get_current_expiry added to token_manager.py")
