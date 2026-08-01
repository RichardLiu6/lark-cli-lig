"""Edge-case tests for `lark-lig approval my` partial-failure handling.

Run: <repo>/.venv/bin/python test_approval_my.py
"""
import re
import sys
from unittest.mock import patch

from click.testing import CliRunner

from lark_cli_lig.api import LarkAPIError
from lark_cli_lig.commands.approval import approval, INCOMPLETE_MARKER

UUID_RE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", re.I)

TYPES = ["purchase", "reimbursement", "overtime", "leave", "payment"]
CODE_OF = {
    "purchase": "1C6CDDFA-3222-4D4E-A2E3-1BB2C6B5CA84",
    "reimbursement": "89618889-E377-43E4-86E3-A421CFB4B845",
    "overtime": "8FF508FD-EC2B-4625-90C0-5C695DA80084",
    "leave": "4EFC010E-2264-49EC-ACB4-5B520828F3C7",
    "payment": "71CC45C9-955B-4CF3-9D4A-A65E8E66E2BA",
}
TYPE_OF_CODE = {v: k for k, v in CODE_OF.items()}

# Each type gets 2 instances; payment's are the NEWEST so their absence is obvious.
START = {"payment": 900, "leave": 800, "overtime": 700, "reimbursement": 600, "purchase": 500}


def fake_instances(atype):
    base = START[atype]
    tag = TYPES.index(atype)  # keep every char hex so the UUID regex matches
    return [
        {
            "instance": {"code": f"{base + i:08X}-0000-0000-0000-{tag:012X}",
                         "status": "approved", "start_time": str(base + i)},
            "approval": {"name": atype},
        }
        for i in (1, 2)
    ]


def make_post(fail_plan):
    """fail_plan: {type: n} -> that type raises on its first n attempts."""
    calls = {}

    def _post(self, path, body=None):
        acode = (body or {}).get("approval_code")
        atype = TYPE_OF_CODE.get(acode, "?")
        calls[atype] = calls.get(atype, 0) + 1
        if calls[atype] <= fail_plan.get(atype, 0):
            raise LarkAPIError(code=1395001, msg="internal error", log_id="TEST")
        return {"instance_list": fake_instances(atype)}

    return _post, calls


def run(fail_plan, args=("my", "-n", "20")):
    _post, calls = make_post(fail_plan)
    with patch("lark_cli_lig.api.LarkAPI.post", _post), \
         patch("lark_cli_lig.commands.approval._QUERY_RETRY_DELAY", 0):
        res = CliRunner().invoke(approval, list(args))
    return res, calls


FAILURES = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


print("\n=== case 1: all types OK (baseline, must behave exactly as before) ===")
res, calls = run({})
check("exit code 0", res.exit_code == 0, f"got {res.exit_code}")
check("no INCOMPLETE marker", INCOMPLETE_MARKER not in res.stdout)
check("all 10 instances listed", len(UUID_RE.findall(res.stdout)) == 10,
      f"got {len(UUID_RE.findall(res.stdout))}")
check("each type queried exactly once", all(calls[t] == 1 for t in TYPES), str(calls))

print("\n=== case 2: payment fails permanently (the 2026-07-31 bug) ===")
res, calls = run({"payment": 99})
check("exit code still 0 (no breakage for callers)", res.exit_code == 0, f"got {res.exit_code}")
check("INCOMPLETE marker on STDOUT (survives 2>/dev/null)", INCOMPLETE_MARKER in res.stdout)
check("INCOMPLETE marker on STDERR too", INCOMPLETE_MARKER in res.stderr)
check("names the failed type", "payment" in res.stdout)
check("names the error code", "1395001" in res.stdout)
check("payment retried exactly twice", calls["payment"] == 2, str(calls))
check("healthy types not retried", all(calls[t] == 1 for t in TYPES if t != "payment"), str(calls))
check("only 8 instances (payment's 2 missing)", len(UUID_RE.findall(res.stdout)) == 8,
      f"got {len(UUID_RE.findall(res.stdout))}")
marker_line = [l for l in res.stdout.splitlines() if INCOMPLETE_MARKER in l][0]
check("marker line contains NO uuid (won't pollute hook grep)", not UUID_RE.search(marker_line),
      marker_line)

print("\n=== case 3: payment fails once then recovers (self-heal) ===")
res, calls = run({"payment": 1})
check("exit code 0", res.exit_code == 0)
check("NO marker — retry healed it", INCOMPLETE_MARKER not in res.stdout)
check("all 10 instances present", len(UUID_RE.findall(res.stdout)) == 10,
      f"got {len(UUID_RE.findall(res.stdout))}")
check("payment called twice", calls["payment"] == 2, str(calls))

print("\n=== case 4: every type fails ===")
res, calls = run({t: 99 for t in TYPES})
check("exit code 0", res.exit_code == 0)
check("marker present", INCOMPLETE_MARKER in res.stdout)
check("reports 5 failed types", "5 approval type(s)" in res.stdout, res.stdout[:200])
check("all five names listed", all(t in res.stdout for t in TYPES))
check("no instances listed", len(UUID_RE.findall(res.stdout)) == 0)

print("\n=== case 5: multiple (not all) types fail ===")
res, calls = run({"payment": 99, "leave": 99})
check("marker reports exactly 2", "2 approval type(s)" in res.stdout)
check("6 instances remain", len(UUID_RE.findall(res.stdout)) == 6,
      f"got {len(UUID_RE.findall(res.stdout))}")

print("\n=== case 6: --type payment (explicit single type) still hard-fails ===")
res, calls = run({"payment": 99}, args=("my", "--type", "payment"))
check("non-zero exit (explicit path unchanged)", res.exit_code != 0, f"got {res.exit_code}")
check("no silent marker path", INCOMPLETE_MARKER not in res.stdout)

print("\n=== case 7: --type payment healthy (regression check) ===")
res, calls = run({}, args=("my", "--type", "payment"))
check("exit 0", res.exit_code == 0)
check("2 payment instances", len(UUID_RE.findall(res.stdout)) == 2)
check("single API call, no retry loop on this path", calls["payment"] == 1, str(calls))

print("\n=== case 8: --status filter still works with partial failure ===")
res, calls = run({"payment": 99}, args=("my", "-n", "20", "--status", "approved"))
check("marker still shown alongside filter", INCOMPLETE_MARKER in res.stdout)
check("8 approved instances", len(UUID_RE.findall(res.stdout)) == 8,
      f"got {len(UUID_RE.findall(res.stdout))}")

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAILED ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
print("ALL CLI TESTS PASSED")
