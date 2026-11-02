"""
generate_evaluation_data_v2.py

Second evaluation dataset pair with partial overlap.
  - Base population: 15,000 records
  - File A: records 0–9,999      (10,000 records)
  - File B: records 5,000–14,999 (10,000 records)
  - True match pairs: records 5,000–9,999 = 5,000 matches (50% overlap)
  - Unmatched in A: records 0–4,999      (5,000 records with no counterpart in B)
  - Unmatched in B: records 10,000–14,999 (5,000 records with no counterpart in A)

Same schemas and noise rules as v1.
"""

import os
import random
import string
import pandas as pd
from faker import Faker
from datetime import timedelta

SEED       = 99
N_POP      = 15_000
N_FILE     = 10_000
OVERLAP_START = 5_000   # first record index shared between A and B (50% overlap)
OUTPUT_DIR = "Datasets for Benchmark"

random.seed(SEED)
fake = Faker("en_US")
Faker.seed(SEED)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Noise helpers ────────────────────────────────────────────────────────────

def char_typo(s, n=1):
    if not s or len(s) < 3:
        return s
    chars = list(s)
    for _ in range(n):
        i  = random.randint(1, max(1, len(chars) - 1))
        op = random.choice(["substitute", "transpose", "delete", "insert"])
        if op == "substitute":
            chars[i] = random.choice(string.ascii_lowercase)
        elif op == "transpose" and i < len(chars) - 1:
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        elif op == "delete" and len(chars) > 3:
            chars.pop(i)
        elif op == "insert":
            chars.insert(i, random.choice(string.ascii_lowercase))
    return "".join(chars)


def date_noise(d):
    if d.day <= 12 and d.day != d.month:
        options = ["swap_day_month", "swap_day_month", "year_off", "day_off"]
    else:
        options = ["year_off", "day_off"]
    op = random.choice(options)
    try:
        if op == "swap_day_month":
            return d.replace(day=d.month, month=d.day)
        elif op == "year_off":
            return d.replace(year=d.year + random.choice([-1, 1]))
        elif op == "day_off":
            return d + timedelta(days=random.choice([-2, -1, 1, 2]))
    except (ValueError, OverflowError):
        pass
    return d


def ssn_noise(digits):
    i       = random.randint(0, len(digits) - 1)
    new_dig = str((int(digits[i]) + random.randint(1, 9)) % 10)
    return digits[:i] + new_dig + digits[i + 1:]


# ── Generate base population (12,000) ────────────────────────────────────────
print("Generating base population (15,000 records)...")
population = []
for i in range(N_POP):
    gender = random.choice(["Male", "Female"])
    dob    = fake.date_of_birth(minimum_age=1, maximum_age=95)
    ssn    = (f"{random.randint(100, 999)}"
              f"{random.randint(10,  99)}"
              f"{random.randint(1000,9999)}")
    mid_roll = random.random()
    if mid_roll < 0.10:
        middle_name = ""
    elif mid_roll < 0.20:
        middle_name = fake.first_name()[0].upper() + "."
    else:
        middle_name = fake.first_name_male() if gender == "Male" \
                      else fake.first_name_female()

    population.append({
        "id":          i,
        "first_name":  fake.first_name_male() if gender == "Male"
                       else fake.first_name_female(),
        "middle_name": middle_name,
        "last_name":   fake.last_name(),
        "dob":         dob,
        "ssn":         ssn,
        "street":      fake.street_address(),
        "city":        fake.city(),
        "state":       fake.state_abbr(),
        "zip_code":    fake.zipcode(),
        "gender":      gender,
    })

# ── File A: records 0–9,999 (registry schema) ────────────────────────────────
print("Building File A (records 0–9,999)...")
rows_a = []
for p in population[:N_FILE]:
    rows_a.append({
        "rec_id":        f"rec-{p['id']:05d}-a",
        "first_name":    p["first_name"],
        "middle_name":   p["middle_name"],
        "last_name":     p["last_name"],
        "date_of_birth": p["dob"].strftime("%Y-%m-%d"),
        "ssn":           f"{p['ssn'][:3]}-{p['ssn'][3:5]}-{p['ssn'][5:]}",
        "street":        p["street"],
        "city":          p["city"],
        "state":         p["state"],
        "zip_code":      p["zip_code"],
        "gender":        p["gender"],
    })
df_a = pd.DataFrame(rows_a).set_index("rec_id")
df_a.to_csv(os.path.join(OUTPUT_DIR, "file_a.csv"))
print(f"  {len(df_a)} records | columns: {list(df_a.columns)}")

# ── File B: records 5,000–14,999 (EHR schema + noise) ───────────────────────
print("Building File B (records 5,000–14,999, with noise)...")

P = {
    "name_typo":  0.15,
    "date_noise": 0.15,
    "ssn_noise":  0.10,
    "addr_typo":  0.10,
    "missing":    0.07,
}
MAX_NOISE = 2

rows_b       = []
noise_counts = []

for p in population[OVERLAP_START : OVERLAP_START + N_FILE]:
    n_applied = 0
    first  = p["first_name"]
    middle = p["middle_name"]
    last   = p["last_name"]
    dob    = p["dob"]
    ssn    = p["ssn"]
    street = p["street"]
    city   = p["city"]
    postal = p["zip_code"]
    sex    = "M" if p["gender"] == "Male" else "F"

    if n_applied < MAX_NOISE and random.random() < P["name_typo"]:
        name_targets = ["first", "last"]
        if middle and len(middle) > 2:
            name_targets.append("middle")
        target = random.choice(name_targets)
        if   target == "first":  first  = char_typo(first)
        elif target == "middle": middle = char_typo(middle)
        elif target == "last":   last   = char_typo(last)
        n_applied += 1

    if n_applied < MAX_NOISE and random.random() < P["date_noise"]:
        dob = date_noise(dob)
        n_applied += 1

    if n_applied < MAX_NOISE and random.random() < P["ssn_noise"]:
        ssn = ssn_noise(ssn)
        n_applied += 1

    if n_applied < MAX_NOISE and random.random() < P["addr_typo"]:
        street = char_typo(street, n=2)
        n_applied += 1

    if n_applied < MAX_NOISE and random.random() < P["missing"]:
        blank = random.choice(["middle_name", "dob", "ssn", "full_address", "sex"])
        if   blank == "middle_name":  middle = ""
        elif blank == "dob":          dob = None
        elif blank == "ssn":          ssn = ""
        elif blank == "full_address": street, city, postal = "", "", ""
        elif blank == "sex":          sex = ""
        n_applied += 1

    noise_counts.append(n_applied)

    full_address = (
        f"{street}, {city}, {p['state']} {postal}".strip(", ")
        if street else ""
    )
    parts = [x for x in [first, middle, last] if x]
    full_name_val = " ".join(parts) if parts else ""
    dob_val       = dob.strftime("%m/%d/%Y") if dob is not None else ""

    rows_b.append({
        "rec_id":          f"rec-{p['id']:05d}-b",
        "full_name":       full_name_val,
        "dob":             dob_val,
        "social_security": ssn,
        "full_address":    full_address,
        "sex":             sex,
    })

df_b = pd.DataFrame(rows_b).set_index("rec_id")
df_b.to_csv(os.path.join(OUTPUT_DIR, "file_b.csv"))
print(f"  {len(df_b)} records | columns: {list(df_b.columns)}")
print(f"  Noise: {noise_counts.count(0)} clean, "
      f"{noise_counts.count(1)} with 1 error, "
      f"{noise_counts.count(2)} with 2 errors")

# ── Ground truth: only the 5,000 overlapping pairs ───────────────────────────
overlap_ids = range(OVERLAP_START, N_FILE)   # 5,000 – 9,999
gt = pd.DataFrame({
    "rec_id_a": [f"rec-{i:05d}-a" for i in overlap_ids],
    "rec_id_b": [f"rec-{i:05d}-b" for i in overlap_ids],
})
gt.to_csv(os.path.join(OUTPUT_DIR, "ground_truth.csv"), index=False)

print(f"\nTrue match pairs : {len(gt):,}  (50% overlap)")
print(f"Unmatched in A   : {OVERLAP_START:,}  (records 0–4,999)")
print(f"Unmatched in B   : {N_POP - N_FILE:,}  (records 10,000–14,999)")
print(f"Files written to : {os.path.abspath(OUTPUT_DIR)}")
