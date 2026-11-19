#!/bin/bash
# Wipe the audit DB and replay the scenario chain in order.
cd /home/user/AUDIT_2026-11-18/r2 || exit 1
rm -f test_db/audit_test.db test_db/audit_test.db-wal test_db/audit_test.db-shm evidence/results.json
for f in "$@"; do
  python3 "tools/$f.py" 2>&1 | grep -v "INFO \[" | grep -v "WARNING \[app\]" | grep -E "^\s+\[|^==|done|Error|error" | cut -c1-200
done
