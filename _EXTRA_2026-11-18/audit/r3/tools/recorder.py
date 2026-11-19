"""Tiny result recorder shared by the audit steps (append-only JSON)."""
import json
import os

PATH = os.environ.get("AMS_AUDIT_RESULTS") or "/home/user/AUDIT_2026-11-18/r3/evidence/results.json"


def load():
    if os.path.exists(PATH):
        with open(PATH) as fh:
            return json.load(fh)
    return []


def record(test_id, module, page, scenario, expected, actual, verdict,
           severity=None, evidence="", note=""):
    rows = load()
    rows = [r for r in rows if r["id"] != test_id]
    rows.append({
        "id": test_id, "module": module, "page": page, "scenario": scenario,
        "expected": expected, "actual": actual, "verdict": verdict,
        "severity": severity, "evidence": evidence, "note": note,
    })
    with open(PATH, "w") as fh:
        json.dump(rows, fh, indent=2, default=str)
    flag = {"PASS": "PASS", "FAIL": "FAIL", "BLOCKED": "BLOCK", "NOT TESTED": "N/T ",
            "INFO": "INFO", "PARTIAL": "PART"}.get(verdict, verdict)
    print(f"  [{flag}] {test_id:<12} {scenario[:64]:<64} exp={expected} act={actual}")
    return verdict


def summary():
    rows = load()
    out = {}
    for r in rows:
        out[r["verdict"]] = out.get(r["verdict"], 0) + 1
    return out, rows
