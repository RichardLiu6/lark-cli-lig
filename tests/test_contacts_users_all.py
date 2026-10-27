"""Regression tests for `lark-lig users-all` / `users` pagination + nested depts.

2026-10-27 bug: second-level departments (Production/Quality/... under a
level-1 parent) were never listed, and neither list endpoint followed
page_token, so whole teams (e.g. Manny) looked absent from Lark.

Run: <repo>/.venv/bin/python tests/test_contacts_users_all.py
"""
import sys
from unittest.mock import patch

from click.testing import CliRunner

from lark_cli_lig.api import LarkAPIError
from lark_cli_lig.commands.approval import INCOMPLETE_MARKER
from lark_cli_lig.commands.contacts import users, users_all

# Tree: root -> L1 "Ops" -> L2 "Production" / "Quality";  root -> L1 "Admin" -> L2 "HR"
L1 = [{"open_department_id": "od-ops", "name": "Ops"},
      {"open_department_id": "od-admin", "name": "Admin"}]
L2 = [{"open_department_id": "od-prod", "name": "Production"},
      {"open_department_id": "od-qa", "name": "Quality"},
      {"open_department_id": "od-hr", "name": "HR"}]


def u(name):
    return {"name": name, "email": f"{name.lower()}@x.com", "open_id": f"ou_{name.lower()}"}


# Production has 3 users split over 2 pages; root has 2 pages too.
USERS = {
    "0": [[u("Richard")], [u("Brandy")]],
    "od-ops": [[]],
    "od-admin": [[u("Brandy")]],  # dup of root -> must be deduped
    "od-prod": [[u("Manny"), u("Josue")], [u("Bing")]],
    "od-qa": [[u("Peggy")]],
    "od-hr": [[u("Cari")]],
}


def page(pages, token):
    i = int(token or 0)
    has_more = i + 1 < len(pages)
    return {"items": pages[i], "has_more": has_more,
            "page_token": str(i + 1) if has_more else ""}


def make_get(fail_depts=(), fail_dept_list=False):
    calls = []

    def _get(self, path, params=None):
        params = params or {}
        calls.append((path, dict(params)))
        if path.endswith("/departments/0/children"):
            if fail_dept_list:
                raise LarkAPIError(code=40004, msg="no dept authority", log_id="T")
            # Emulate Lark: only fetch_child=true returns nested levels.
            depts = L1 + L2 if params.get("fetch_child") == "true" else L1
            return page([depts[:3], depts[3:]], params.get("page_token"))
        if path == "/open-apis/contact/v3/users":
            dept = params["department_id"]
            if dept in fail_depts:
                raise LarkAPIError(code=41050, msg="no user authority", log_id="T")
            return page(USERS[dept], params.get("page_token"))
        raise AssertionError(f"unexpected path {path}")

    return _get, calls


def run(cmd, args=(), **kw):
    _get, calls = make_get(**kw)
    with patch("lark_cli_lig.api.LarkAPI.get", _get), \
         patch("lark_cli_lig.commands.contacts.TokenManager"):
        res = CliRunner().invoke(cmd, list(args), obj={"identity": "bot"})
    return res, calls


FAILURES = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def names(stdout):
    return [l.split("|")[1].strip() for l in stdout.splitlines() if l.count("|") == 3]


print("\n=== case 1: nested depts + pagination (the 2026-10-27 bug) ===")
res, calls = run(users_all)
got = names(res.stdout)
check("exit 0", res.exit_code == 0, f"{res.exit_code} {res.output}")
for who in ("Manny", "Josue", "Bing", "Peggy", "Cari"):
    check(f"{who} (L2 dept) listed", who in got, str(got))
check("page-2 users present (Bing, Brandy)", "Bing" in got and "Brandy" in got, str(got))
check("Brandy deduped", got.count("Brandy") == 1, str(got))
check("7 unique users", len(got) == 7, str(got))
check("fetch_child=true sent", any(p.get("fetch_child") == "true" for _, p in calls))
check("summary on stderr", "Total: 7 users across 6 departments (0 failed)" in res.stderr, res.stderr)
check("no INCOMPLETE marker", INCOMPLETE_MARKER not in res.stdout)
check("Manny tagged with Production", any(l.startswith("Production") and "Manny" in l
                                          for l in res.stdout.splitlines()))

print("\n=== case 2: one dept fails -> loud error + marker, rest still listed ===")
res, _ = run(users_all, fail_depts=("od-qa",))
got = names(res.stdout)
check("exit 0 (partial, same contract as approval my)", res.exit_code == 0)
check("error names dept on stderr", "Quality" in res.stderr and "41050" in res.stderr, res.stderr)
check("INCOMPLETE marker on stdout", INCOMPLETE_MARKER in res.stdout)
check("INCOMPLETE marker on stderr", INCOMPLETE_MARKER in res.stderr)
check("Peggy missing, others present", "Peggy" not in got and "Manny" in got, str(got))
check("summary counts failure", "(1 failed)" in res.stderr, res.stderr)

print("\n=== case 3: department listing fails -> non-zero exit ===")
res, _ = run(users_all, fail_dept_list=True)
check("exit != 0", res.exit_code != 0, str(res.exit_code))
check("error on stderr", "Failed to list departments" in res.stderr, res.stderr)

print("\n=== case 4: `users` follows pagination ===")
res, _ = run(users)
check("exit 0", res.exit_code == 0, res.output)
check("both root pages listed", "Found 2 users" in res.stdout and "Brandy" in res.stdout, res.stdout)

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAILED ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
print("ALL CONTACTS TESTS PASSED")
