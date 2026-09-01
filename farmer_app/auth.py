"""
A gate on the door. Not an identity system.

WHAT THIS IS, EXACTLY
---------------------
A password check with per-user scoping, so that one deployment can serve
several block officers without each of them seeing the others' tenancies. It
stops the wrong colleague from opening the wrong block. That is a real and
useful thing.

WHAT IT IS NOT
--------------
It is not protection against somebody on the network. Streamlit sends the
password over whatever transport it was given, and if that is plain HTTP then
the password crosses the network in the clear no matter how it is stored here.
It has no session expiry worth the name, no rate limiting beyond a crude one,
no audit trail, no password reset, and no second factor.

So: this is correct behind a reverse proxy that terminates TLS and does its own
authentication, or on a machine only the operator can reach. It is NOT
sufficient on its own for a public address holding real tenancy records, and
the login screen says so rather than implying a safety it does not provide. A
security control that overstates itself is worse than none, because people
stop taking the other precautions.

HOW PASSWORDS ARE STORED
------------------------
PBKDF2-HMAC-SHA256 with a per-user random salt and a high iteration count, from
the standard library. Not because this file is the last line of defence - it is
not - but because people reuse passwords, and a stolen users file should not
hand somebody else's password to whoever took it.

Comparison is constant-time. The timing signal here is small; using the
constant-time function anyway costs nothing and removes the question.

THERE IS NO DEFAULT PASSWORD
----------------------------
A deployment with no users file is OPEN and says so on every screen. It does
not fall back to "admin/admin", because a default credential is a credential
everybody has, and a tool that ships one has shipped a vulnerability with a
support burden attached.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

import streamlit as st


USERS_FILE = "users.json"
ITERATIONS = 240_000
# Crude, and enough for a field office: after this many failures in one browser
# session the gate stops answering. It is not rate limiting for the internet.
MAX_ATTEMPTS = 8

OPEN_WARNING = (
    "No users file, so this deployment is OPEN: anyone who can reach this "
    "address can read every field. Create one with "
    "`python -m farmer_app.auth add <name>` before putting it anywhere others "
    "can reach.")
OPEN_WARNING_AR = (
    "لا ملف مستخدمين، فهذا التشغيل مفتوح: كل من يصل إلى هذا العنوان يقرأ كل "
    "حقل. أنشئ ملفًا قبل وضعه في مكان يصله غيرك.")

NOT_ENOUGH = (
    "This is a gate, not transport security. Put it behind HTTPS and your own "
    "authentication before it is reachable from a network you do not control.")
NOT_ENOUGH_AR = (
    "هذه بوّابة لا حماية نقل. ضعها خلف HTTPS واستيثاقك الخاصّ قبل أن تكون "
    "قابلة للوصول من شبكة لا تتحكّم فيها.")


# ==============================================================================
# THE USERS FILE
# ==============================================================================

def hash_password(password: str, salt: Optional[str] = None) -> dict:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), ITERATIONS)
    return {"salt": salt, "hash": dk.hex(), "iterations": ITERATIONS,
            "algorithm": "pbkdf2-hmac-sha256"}


def verify_password(password: str, record: dict) -> bool:
    """Constant-time comparison. The timing signal here is small; using the
    constant-time function anyway costs nothing and removes the question."""
    try:
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(record["salt"]),
            int(record.get("iterations", ITERATIONS)))
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(dk.hex(), record.get("hash", ""))


def load_users(path: str = USERS_FILE) -> Optional[dict]:
    """The users file, or None when there is none.

    None means OPEN. It is a distinct state from "a file with no users in it",
    which means nobody can get in - and both are honest answers that the caller
    must be able to tell apart.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        # An unreadable users file must NOT fall open. A gate that opens when
        # its configuration is corrupt is worse than no gate, because nobody
        # is watching for it.
        return {"users": {}, "error": "the users file could not be read"}


def save_users(users: dict, path: str = USERS_FILE) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2, ensure_ascii=False)
    # Best effort on POSIX; Windows ACLs are the operator's business.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def add_user(name: str, password: str, farms=None, role: str = "officer",
             path: str = USERS_FILE) -> dict:
    users = load_users(path) or {"users": {}}
    users.setdefault("users", {})[name] = {
        "password": hash_password(password),
        # None means every farm. A list scopes the user to those farms only.
        "farms": list(farms) if farms else None,
        "role": role,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    save_users(users, path)
    return {"name": name, "farms": farms, "role": role}


def authenticate(name: str, password: str,
                 path: str = USERS_FILE) -> Optional[dict]:
    users = load_users(path)
    if users is None:
        return None
    rec = (users.get("users") or {}).get(name)
    if not rec:
        # Still run a hash, so a missing user and a wrong password take
        # comparable time and the gate does not enumerate accounts for free.
        hash_password(password)
        return None
    if not verify_password(password, rec.get("password", {})):
        return None
    return {"name": name, "farms": rec.get("farms"),
            "role": rec.get("role", "officer")}


def may_see(user: Optional[dict], farm: str) -> bool:
    """Scoping. A user with no farm list sees everything; one with a list sees
    only those farms."""
    if user is None:
        return True                       # open deployment
    farms = user.get("farms")
    return farms is None or farm in farms


# ==============================================================================
# THE GATE
# ==============================================================================

def gate(path: str = USERS_FILE, ar: bool = False) -> Optional[dict]:
    """
    Show the login and stop the script until it passes.

    Returns the signed-in user, or None when the deployment is open. Callers
    must treat None as "open", not as "denied": the two are different, and
    conflating them is how a gate ends up either useless or unopenable.
    """
    users = load_users(path)
    if users is None:
        st.sidebar.warning(OPEN_WARNING_AR if ar else OPEN_WARNING)
        return None

    if st.session_state.get("_user"):
        u = st.session_state["_user"]
        with st.sidebar:
            st.caption(f"{u['name']} · {u.get('role', '')}")
            if st.button("خروج" if ar else "Sign out"):
                st.session_state.pop("_user", None)
                st.rerun()
        return u

    st.markdown("### " + ("تسجيل الدخول" if ar else "Sign in"))
    if users.get("error"):
        st.error(users["error"])
        st.stop()

    tries = st.session_state.get("_auth_tries", 0)
    if tries >= MAX_ATTEMPTS:
        st.error("توقّفت المحاولات. أعد تحميل الصفحة." if ar else
                 "Too many attempts. Reload the page.")
        st.stop()

    with st.form("signin"):
        name = st.text_input("الاسم" if ar else "Name")
        pw = st.text_input("كلمة المرور" if ar else "Password", type="password")
        if st.form_submit_button("دخول" if ar else "Sign in"):
            user = authenticate(name, pw, path)
            if user:
                st.session_state["_user"] = user
                st.session_state["_auth_tries"] = 0
                st.rerun()
            st.session_state["_auth_tries"] = tries + 1
            # One message for both failures: telling somebody the name was
            # right is telling them half the answer.
            st.error("الاسم أو كلمة المرور غير صحيحة." if ar else
                     "Name or password is incorrect.")

    st.caption(NOT_ENOUGH_AR if ar else NOT_ENOUGH)
    st.stop()


# ==============================================================================
# COMMAND LINE
# ==============================================================================

def _cli():
    import argparse
    import getpass
    p = argparse.ArgumentParser(
        description="Manage the users file. There is no default password: a "
                    "deployment with no users file is OPEN and says so.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add or replace a user")
    a.add_argument("name")
    a.add_argument("--farms", nargs="*", default=None,
                   help="restrict this user to these farms; omit for all")
    a.add_argument("--role", default="officer")
    a.add_argument("--file", default=USERS_FILE)
    ls = sub.add_parser("list", help="list users")
    ls.add_argument("--file", default=USERS_FILE)
    args = p.parse_args()

    if args.cmd == "add":
        pw = getpass.getpass("Password: ")
        again = getpass.getpass("Again: ")
        if pw != again:
            print("They do not match.")
            raise SystemExit(1)
        if len(pw) < 10:
            # Not a policy engine. One floor, because this gate is the only
            # thing between a guess and somebody's tenancy records.
            print("Too short - use at least 10 characters.")
            raise SystemExit(1)
        out = add_user(args.name, pw, args.farms, args.role, args.file)
        print(f"Added {out['name']} ({out['role']}) to {args.file}")
        print(NOT_ENOUGH)
    else:
        users = load_users(args.file)
        if users is None:
            print(OPEN_WARNING)
            return
        for name, rec in (users.get("users") or {}).items():
            scope = rec.get("farms") or "all farms"
            print(f"{name:24} {rec.get('role', ''):12} {scope}")


if __name__ == "__main__":
    _cli()
