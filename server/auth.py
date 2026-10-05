"""One administrator account and revocable, server-side login sessions."""

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from sqlalchemy import delete
from sqlmodel import Field, Session, SQLModel

COOKIE_NAME = "nasflow_admin"
SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000


class AdminAccount(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    username: str
    salt: str
    password_hash: str


class AdminSession(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    expires_at: float


class OwnerMediaToken(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    task_id: str
    admin_session_hash: str
    expires_at: float


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                               PASSWORD_ITERATIONS).hex()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def initialize_admin(engine, data_dir: Path, *, username: str | None = None,
                     password: str | None = None) -> None:
    with Session(engine) as session:
        if session.get(AdminAccount, 1):
            return
        username = username or os.getenv("NASFLOW_ADMIN_USERNAME", "admin").strip()
        password = password or os.getenv("NASFLOW_ADMIN_PASSWORD")
        generated = not password
        if generated:
            password = secrets.token_urlsafe(24)
        if not username or len(username) > 64 or not password or not 8 <= len(password) <= 128:
            raise ValueError("管理员账号不能为空；密码应为 8–128 个字符")
        salt = secrets.token_hex(32)
        account = AdminAccount(username=username, salt=salt, password_hash=password_hash(password, salt))
        if generated:
            # A local NAS owner can read this once; it is never served by the API.
            path = data_dir / "admin-initial-password.txt"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(f"NASFlow initial administrator\nUsername: {username}\nPassword: {password}\n")
            path.chmod(0o600)
        session.add(account)
        session.commit()


def verify_login(engine, username: str, password: str) -> str | None:
    with Session(engine) as session:
        account = session.get(AdminAccount, 1)
        if not account:
            return None
        valid = hmac.compare_digest(password_hash(password, account.salt), account.password_hash)
        return account.username if valid and hmac.compare_digest(username.encode(), account.username.encode()) else None


def create_session(engine) -> str:
    token = secrets.token_urlsafe(32)
    with Session(engine) as session:
        session.exec(delete(AdminSession).where(AdminSession.expires_at <= time.time()))
        session.add(AdminSession(token_hash=token_hash(token), expires_at=time.time() + SESSION_SECONDS))
        session.commit()
    return token


def session_username(engine, token: str | None) -> str | None:
    if not token or len(token) > 128:
        return None
    with Session(engine) as session:
        saved = session.get(AdminSession, token_hash(token))
        if not saved or saved.expires_at <= time.time():
            return None
        account = session.get(AdminAccount, 1)
        return account.username if account else None


def logout(engine, token: str) -> None:
    with Session(engine) as session:
        session.exec(delete(AdminSession).where(AdminSession.token_hash == token_hash(token)))
        session.commit()


def change_credentials(engine, data_dir: Path, username: str, current_password: str,
                       new_password: str) -> bool:
    with Session(engine) as session:
        account = session.get(AdminAccount, 1)
        if not account or not hmac.compare_digest(password_hash(current_password, account.salt), account.password_hash):
            return False
        account.username = username.strip()
        account.salt = secrets.token_hex(32)
        account.password_hash = password_hash(new_password, account.salt)
        session.add(account)
        session.exec(delete(AdminSession))
        session.commit()
    (data_dir / "admin-initial-password.txt").unlink(missing_ok=True)
    return True
