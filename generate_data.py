"""
generate_data.py
Generates a synthetic cases.json file for import testing.
Run: python generate_data.py
Produces: cases.json (5000 records)
"""

import json
import random
from datetime import datetime, timedelta

random.seed(42)

CASE_TYPES = ["Type A", "Type B", "Type C", "Type D", "Type E"]
STATUSES   = [
    "received", "received", "received",
    "approved", "approved",
    "rfe", "rfer",
    "processing", "processing",
    "rejected",
    "transferred",
    "withdrawn",
    "denied",
]
TITLES = {
    "received":    "Case Was Received and A Receipt Notice Was Sent",
    "approved":    "Case Was Approved",
    "rfe":         "Request for Evidence Was Sent",
    "rfer":        "Response to Request for Evidence Was Received",
    "processing":  "Case Is Being Actively Reviewed",
    "rejected":    "Case Was Rejected",
    "transferred": "Case Was Transferred to a New Office",
    "withdrawn":   "Application Was Withdrawn",
    "denied":      "Case Was Denied",
}

START_DATE = datetime(2023, 1, 1)
END_DATE   = datetime(2025, 8, 31)

def random_date(start, end):
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))

def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

records = []
used_numbers = set()

while len(records) < 5000:
    ct     = random.choice(CASE_TYPES)
    num    = f"RD2{random.randint(100000, 999999)}"
    if num in used_numbers:
        continue
    used_numbers.add(num)

    status = random.choice(STATUSES)
    filed  = random_date(START_DATE, END_DATE)

    # If received, last updated = filed date (not yet worked)
    if status == "received":
        updated = filed
    else:
        updated = random_date(filed, min(filed + timedelta(days=400), END_DATE))

    records.append({
        "Case Number":            num,
        "Service Center":         "RD2",
        "Case Type":              ct,
        "Case Status":            status,
        "Title":                  TITLES[status],
        "Application Filed Date": fmt(filed),
        "Last Updated Date":      fmt(updated),
    })

with open("cases.json", "w") as f:
    json.dump(records, f, indent=2)

print(f"Generated {len(records)} records → cases.json")

# Quick sanity check
received = [r for r in records if r["Case Status"] == "received"]
mismatched = [r for r in received if r["Application Filed Date"] != r["Last Updated Date"]]
print(f"Received records: {len(received)}")
print(f"Mismatched dates in received: {len(mismatched)} (should be 0)")