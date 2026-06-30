from pathlib import Path

p = Path("data_fetcher.py")
txt = p.read_text(encoding="utf-8")

old = '''        if call_coi == 0 and put_coi == 0:
            raise RuntimeError("COI unavailable in option chain")
        ratio = round((put_coi / call_coi), 2) if call_coi else 9.99'''

new = '''        # COI can be zero during market-close or first snapshot.
        # Keep it available as neutral instead of blocking the factor.
        ratio = round((put_coi / call_coi), 2) if call_coi else (1.0 if put_coi == 0 else 9.99)'''

if old not in txt:
    print("Target COI block not found or already fixed.")
else:
    p.write_text(txt.replace(old, new), encoding="utf-8")
    print("COI zero-neutral availability fixed.")
