"""
auth.py — Autenticação completa com política de senha, lockout e auditoria.
"""
from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session


# ── Hashing ────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Política de senha ──────────────────────────────────────────────────────────

def get_policy(db: Session):
    """Retorna a linha singleton de PasswordPolicy. Cria se não existir."""
    from app.models import PasswordPolicy
    policy = db.query(PasswordPolicy).first()
    if policy is None:
        policy = PasswordPolicy()
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return policy


def validate_password(password: str, policy) -> list[str]:
    """Retorna lista de erros. Lista vazia = senha válida."""
    errors: list[str] = []
    if len(password) < policy.min_length:
        errors.append(f"Mínimo de {policy.min_length} caracteres.")
    if policy.require_uppercase and not re.search(r"[A-Z]", password):
        errors.append("Deve conter pelo menos uma letra maiúscula.")
    if policy.require_lowercase and not re.search(r"[a-z]", password):
        errors.append("Deve conter pelo menos uma letra minúscula.")
    if policy.require_digits and not re.search(r"\d", password):
        errors.append("Deve conter pelo menos um número.")
    if policy.require_special and not re.search(r"[^A-Za-z0-9]", password):
        errors.append("Deve conter pelo menos um caractere especial (!@#$%...).")
    return errors


def is_password_reused(db: Session, user, new_password: str, history_count: int) -> bool:
    """Retorna True se a senha nova já está no histórico recente."""
    if history_count <= 0:
        return False
    from app.models import PasswordHistory
    recent = (
        db.query(PasswordHistory)
        .filter(PasswordHistory.user_id == user.id)
        .order_by(PasswordHistory.changed_at.desc())
        .limit(history_count)
        .all()
    )
    return any(verify_password(new_password, h.password_hash) for h in recent)


def set_password(
    db: Session,
    user,
    new_password: str,
    policy,
    actor: Optional[str] = None,
    forced: bool = False,
) -> None:
    """
    Hash + salva nova senha, registra no histórico, atualiza expiração.
    Registra evento de auditoria.
    """
    from app.models import PasswordHistory

    # Salvar hash anterior no histórico (se usuário já tem senha)
    if user.password_hash:
        db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))

    user.password_hash = hash_password(new_password)
    user.last_password_change = datetime.utcnow()
    user.must_change_password = False

    # Calcular expiração
    if policy.expiry_days > 0:
        user.password_expires_at = datetime.utcnow() + timedelta(days=policy.expiry_days)
    else:
        user.password_expires_at = None

    db.commit()

    # Limpar histórico antigo (manter apenas os últimos `history_count`)
    if policy.history_count > 0:
        all_hist = (
            db.query(PasswordHistory)
            .filter(PasswordHistory.user_id == user.id)
            .order_by(PasswordHistory.changed_at.desc())
            .all()
        )
        for old in all_hist[policy.history_count:]:
            db.delete(old)
        db.commit()

    # Auditoria
    if forced:
        event_type = "password_reset_admin"
        details = f"Senha redefinida pelo administrador '{actor}'"
    elif actor == user.username:
        event_type = "password_changed"
        details = "Senha alterada pelo próprio usuário"
    else:
        event_type = "password_changed"
        details = f"Senha alterada (ator: {actor})"

    log_security_event(db, event_type,
                       username=user.username, actor=actor, details=details)


# ── Geração de senha temporária ────────────────────────────────────────────────

def generate_temp_password(length: int = 12) -> str:
    """Gera senha temporária que atende requisitos básicos da política."""
    chars = string.ascii_letters + string.digits + "!@#$"
    while True:
        pwd = "".join(secrets.choice(chars) for _ in range(length))
        # Garantir pelo menos um de cada tipo
        if (re.search(r"[A-Z]", pwd) and re.search(r"[a-z]", pwd)
                and re.search(r"\d", pwd) and re.search(r"[!@#$]", pwd)):
            return pwd


# ── Autenticação ───────────────────────────────────────────────────────────────

def authenticate_user(
    db: Session,
    username: str,
    password: str,
    ip: Optional[str] = None,
) -> tuple:
    """
    Retorna (user, error_message).
    Em caso de sucesso: (user, None).
    Em caso de falha:   (None, mensagem_generica).

    NUNCA revela se o usuário existe ou não na mensagem de erro.
    """
    from app.models import User

    GENERIC_ERROR = "Usuário ou senha incorretos."

    user = db.query(User).filter(User.username == username).first()

    if user is None:
        # Executar verificação falsa para evitar timing attack
        verify_password(password, "$2b$12$fakehashfakehashfakehashfakehashfakehashfakehas")
        log_security_event(db, "login_fail",
                           username=username, ip_address=ip,
                           details="Usuário não encontrado")
        return None, GENERIC_ERROR

    # Verificar se está bloqueado
    if user.locked_until and datetime.utcnow() < user.locked_until:
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
        log_security_event(db, "login_fail",
                           username=username, ip_address=ip,
                           details="Conta bloqueada")
        # Retornar mensagem genérica para não revelar o estado
        return None, f"Conta temporariamente bloqueada. Tente novamente em {remaining} minuto(s)."

    # Verificar conta ativa
    if not user.is_active:
        verify_password(password, user.password_hash)   # timing parity
        log_security_event(db, "login_fail",
                           username=username, ip_address=ip,
                           details="Conta desativada")
        return None, GENERIC_ERROR

    # Verificar senha
    if not verify_password(password, user.password_hash):
        policy = get_policy(db)
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= policy.max_attempts:
            user.locked_until = datetime.utcnow() + timedelta(minutes=policy.lockout_minutes)
            db.commit()
            log_security_event(db, "account_locked",
                               username=username, ip_address=ip,
                               details=f"Conta bloqueada após {user.failed_attempts} tentativas")
            return None, (
                f"Conta bloqueada por {policy.lockout_minutes} minuto(s) "
                f"após {policy.max_attempts} tentativas inválidas."
            )
        db.commit()
        log_security_event(db, "login_fail",
                           username=username, ip_address=ip,
                           details=f"Senha inválida (tentativa {user.failed_attempts})")
        return None, GENERIC_ERROR

    # Sucesso — resetar contador e registrar login
    user.failed_attempts = 0
    user.locked_until = None

    # Checar expiração de senha
    if user.password_expires_at and datetime.utcnow() > user.password_expires_at:
        user.must_change_password = True

    user.last_login = datetime.utcnow()
    db.commit()

    log_security_event(db, "login_success",
                       username=username, actor=username, ip_address=ip)
    return user, None


# ── Log de auditoria ──────────────────────────────────────────────────────────

def log_security_event(
    db: Session,
    event_type: str,
    username: Optional[str] = None,
    actor: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    from app.models import SecurityLog
    try:
        entry = SecurityLog(
            event_type=event_type,
            username=username,
            actor=actor,
            ip_address=ip_address,
            details=details,
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()


# ── Dependências FastAPI ───────────────────────────────────────────────────────

def require_admin(request: Request) -> None:
    """FastAPI Depends — exige perfil administrador na sessão."""
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores.")
