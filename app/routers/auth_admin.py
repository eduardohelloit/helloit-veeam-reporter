"""
auth_admin.py — Rotas de autenticação e administração de usuários.

Rotas públicas-autenticadas (qualquer usuário logado):
  GET/POST /auth/change-password       — troca de senha voluntária
  GET/POST /auth/force-change-password — troca obrigatória (primeiro acesso / expiração)

Rotas administrativas (requer is_admin=True):
  GET      /admin/users                — lista de usuários
  GET/POST /admin/users/new            — criar usuário
  GET/POST /admin/users/{id}/edit      — editar usuário
  POST     /admin/users/{id}/toggle    — ativar / desativar
  POST     /admin/users/{id}/reset-password — resetar senha
  POST     /admin/users/{id}/delete    — excluir usuário
  GET/POST /admin/password-policy      — política de senha
  GET      /admin/security-log         — log de auditoria
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import (
    get_policy, validate_password, is_password_reused,
    set_password, hash_password, generate_temp_password,
    log_security_event, require_admin,
)

router = APIRouter()

BASE_DIR  = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# Globals exigidos pelo base.html (senão as páginas deste router dão 500).
from app.services import branding_service
from app.services.classification_service import situation_badge_class
templates.env.globals["get_branding"]          = branding_service.get_branding
templates.env.globals["situation_badge_class"] = situation_badge_class
templates.env.globals["now"]                   = datetime.now


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _current_user(request: Request, db: Session):
    """Retorna o User da sessão ou levanta 401."""
    from app.models import User
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401)
    user = db.query(User).get(uid)
    if not user:
        raise HTTPException(status_code=401)
    return user


def _policy_description(policy) -> list[str]:
    """Retorna lista de strings descrevendo a política atual."""
    items = [f"Mínimo {policy.min_length} caracteres"]
    if policy.require_uppercase:  items.append("letra maiúscula")
    if policy.require_lowercase:  items.append("letra minúscula")
    if policy.require_digits:     items.append("número")
    if policy.require_special:    items.append("caractere especial")
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Alterar senha (voluntária)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/auth/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, db: Session = Depends(get_db)):
    user   = _current_user(request, db)
    policy = get_policy(db)
    return templates.TemplateResponse("auth/change_password.html", {
        "request": request,
        "policy_items": _policy_description(policy),
        "min_length": policy.min_length,
    })


@router.post("/auth/change-password")
async def change_password_post(
    request: Request,
    current_password: str = Form(...),
    new_password:     str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.auth import verify_password

    user   = _current_user(request, db)
    policy = get_policy(db)
    errors: list[str] = []

    if not verify_password(current_password, user.password_hash):
        errors.append("Senha atual incorreta.")

    if new_password != confirm_password:
        errors.append("As senhas não conferem.")

    if not errors:
        errs = validate_password(new_password, policy)
        errors.extend(errs)

    if not errors and is_password_reused(db, user, new_password, policy.history_count):
        errors.append(
            f"Esta senha já foi usada recentemente. "
            f"Escolha uma das últimas {policy.history_count} senhas diferentes."
        )

    if errors:
        return templates.TemplateResponse("auth/change_password.html", {
            "request": request,
            "errors": errors,
            "policy_items": _policy_description(policy),
            "min_length": policy.min_length,
        }, status_code=400)

    set_password(db, user, new_password, policy, actor=user.username)
    return RedirectResponse(url="/?msg=senha_alterada", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# Troca de senha obrigatória (primeiro acesso / expiração)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/auth/force-change-password", response_class=HTMLResponse)
def force_change_password_page(request: Request, db: Session = Depends(get_db)):
    user   = _current_user(request, db)
    policy = get_policy(db)
    return templates.TemplateResponse("auth/force_change_password.html", {
        "request": request,
        "username": user.username,
        "policy_items": _policy_description(policy),
        "min_length": policy.min_length,
    })


@router.post("/auth/force-change-password")
async def force_change_password_post(
    request: Request,
    new_password:     str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user   = _current_user(request, db)
    policy = get_policy(db)
    errors: list[str] = []

    if new_password != confirm_password:
        errors.append("As senhas não conferem.")

    if not errors:
        errors.extend(validate_password(new_password, policy))

    if not errors and is_password_reused(db, user, new_password, policy.history_count):
        errors.append(
            f"Esta senha já foi usada recentemente. Escolha uma diferente."
        )

    if errors:
        return templates.TemplateResponse("auth/force_change_password.html", {
            "request": request,
            "username": user.username,
            "errors": errors,
            "policy_items": _policy_description(policy),
            "min_length": policy.min_length,
        }, status_code=400)

    set_password(db, user, new_password, policy, actor=user.username)
    # Atualizar sessão
    request.session["must_change_password"] = False
    return RedirectResponse(url="/", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# Gestão de usuários (admin)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import User
    users = db.query(User).order_by(User.username).all()
    return templates.TemplateResponse("auth/users.html", {
        "request": request,
        "users": users,
        "now": datetime.utcnow(),
    })


@router.get("/admin/users/new", response_class=HTMLResponse)
def admin_user_new_page(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    policy = get_policy(db)
    return templates.TemplateResponse("auth/user_form.html", {
        "request": request,
        "edit_user": None,
        "policy_items": _policy_description(policy),
    })


@router.post("/admin/users/new")
async def admin_user_new_post(
    request: Request,
    username:             str  = Form(...),
    full_name:            str  = Form(""),
    email:                str  = Form(""),
    password:             str  = Form(""),
    is_admin:             bool = Form(False),
    must_change_password: bool = Form(True),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import User

    actor  = request.session.get("username", "admin")
    policy = get_policy(db)
    errors: list[str] = []

    username = username.strip()
    if not username:
        errors.append("Nome de usuário obrigatório.")

    # Verificar duplicata
    if username and db.query(User).filter(User.username == username).first():
        errors.append(f"Usuário '{username}' já existe.")

    # Validar senha
    if not password:
        errors.append("Senha obrigatória.")
    else:
        errors.extend(validate_password(password, policy))

    if errors:
        return templates.TemplateResponse("auth/user_form.html", {
            "request": request,
            "edit_user": None,
            "errors": errors,
            "form": {"username": username, "full_name": full_name,
                     "email": email, "is_admin": is_admin,
                     "must_change_password": must_change_password},
            "policy_items": _policy_description(policy),
        }, status_code=400)

    user = User(
        username=username,
        full_name=full_name.strip() or None,
        email=email.strip() or None,
        password_hash=hash_password(password),
        is_admin=is_admin,
        is_active=True,
        must_change_password=must_change_password,
        created_by=actor,
    )
    # Calcular expiração
    if policy.expiry_days > 0 and not must_change_password:
        from datetime import timedelta
        user.password_expires_at = datetime.utcnow() + timedelta(days=policy.expiry_days)

    db.add(user)
    db.commit()
    db.refresh(user)

    # Salvar no histórico
    from app.models import PasswordHistory
    db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
    db.commit()

    log_security_event(db, "user_created",
                       username=username, actor=actor,
                       details=f"Admin={is_admin}, MustChange={must_change_password}")
    return RedirectResponse(url="/admin/users?msg=criado", status_code=303)


@router.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
def admin_user_edit_page(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import User
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    policy = get_policy(db)
    return templates.TemplateResponse("auth/user_form.html", {
        "request": request,
        "edit_user": user,
        "policy_items": _policy_description(policy),
    })


@router.post("/admin/users/{user_id}/edit")
async def admin_user_edit_post(
    user_id:   int,
    request:   Request,
    full_name: str  = Form(""),
    email:     str  = Form(""),
    is_admin:  bool = Form(False),
    is_active: bool = Form(True),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import User

    actor = request.session.get("username", "admin")
    user  = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404)

    # Impedir remover o próprio admin se for o único
    if not is_admin and user.username == actor:
        if db.query(User).filter(User.is_admin.is_(True), User.is_active.is_(True)).count() == 1:
            return templates.TemplateResponse("auth/user_form.html", {
                "request": request,
                "edit_user": user,
                "errors": ["Não é possível remover seu próprio perfil de administrador — é o único admin ativo."],
                "policy_items": _policy_description(get_policy(db)),
            }, status_code=400)

    user.full_name = full_name.strip() or None
    user.email     = email.strip() or None
    user.is_admin  = is_admin
    user.is_active = is_active
    db.commit()

    log_security_event(db, "user_edited",
                       username=user.username, actor=actor,
                       details=f"Admin={is_admin}, Ativo={is_active}")
    return RedirectResponse(url="/admin/users?msg=editado", status_code=303)


@router.post("/admin/users/{user_id}/toggle")
def admin_user_toggle(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import User

    actor = request.session.get("username", "admin")
    user  = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404)

    # Impedir desativar a si mesmo
    if user.username == actor and user.is_active:
        return RedirectResponse(url="/admin/users?err=self_disable", status_code=303)

    user.is_active = not user.is_active
    db.commit()

    action = "user_activated" if user.is_active else "user_disabled"
    log_security_event(db, action, username=user.username, actor=actor)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import User

    actor  = request.session.get("username", "admin")
    user   = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404)

    policy   = get_policy(db)
    temp_pwd = generate_temp_password()

    # Saltar verificação de histórico para reset administrativo
    user.password_hash         = hash_password(temp_pwd)
    user.must_change_password  = True
    user.failed_attempts       = 0
    user.locked_until          = None
    user.last_password_change  = datetime.utcnow()
    user.password_expires_at   = None
    db.commit()

    log_security_event(db, "password_reset_admin",
                       username=user.username, actor=actor,
                       details="Senha temporária gerada pelo administrador")

    # Mostrar a senha temporária ao admin
    return templates.TemplateResponse("auth/users.html", {
        "request": request,
        "users": db.query(User).order_by(User.username).all(),
        "now": datetime.utcnow(),
        "temp_pwd_user": user.username,
        "temp_pwd": temp_pwd,
    })


@router.post("/admin/users/{user_id}/delete")
def admin_user_delete(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import User

    actor = request.session.get("username", "admin")
    user  = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404)

    # Impedir excluir a si mesmo
    if user.username == actor:
        return RedirectResponse(url="/admin/users?err=self_delete", status_code=303)

    # Impedir excluir o único admin
    if user.is_admin:
        admins = db.query(User).filter(User.is_admin.is_(True)).count()
        if admins <= 1:
            return RedirectResponse(url="/admin/users?err=last_admin", status_code=303)

    log_security_event(db, "user_deleted",
                       username=user.username, actor=actor)
    db.delete(user)
    db.commit()
    return RedirectResponse(url="/admin/users?msg=excluido", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# Política de senha (admin)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/password-policy", response_class=HTMLResponse)
def admin_policy_page(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    policy = get_policy(db)
    return templates.TemplateResponse("auth/password_policy.html", {
        "request": request,
        "policy": policy,
    })


@router.post("/admin/password-policy")
async def admin_policy_post(
    request: Request,
    min_length:        int  = Form(8),
    require_uppercase: bool = Form(False),
    require_lowercase: bool = Form(False),
    require_digits:    bool = Form(False),
    require_special:   bool = Form(False),
    history_count:     int  = Form(0),
    expiry_days:       int  = Form(0),
    max_attempts:      int  = Form(5),
    lockout_minutes:   int  = Form(15),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    actor  = request.session.get("username", "admin")
    policy = get_policy(db)
    errors: list[str] = []

    if min_length < 4:
        errors.append("Tamanho mínimo não pode ser menor que 4 caracteres.")
    if max_attempts < 1:
        errors.append("Máximo de tentativas deve ser pelo menos 1.")
    if lockout_minutes < 1:
        errors.append("Tempo de bloqueio deve ser pelo menos 1 minuto.")

    if errors:
        return templates.TemplateResponse("auth/password_policy.html", {
            "request": request,
            "policy": policy,
            "errors": errors,
        }, status_code=400)

    policy.min_length        = min_length
    policy.require_uppercase = require_uppercase
    policy.require_lowercase = require_lowercase
    policy.require_digits    = require_digits
    policy.require_special   = require_special
    policy.history_count     = max(0, history_count)
    policy.expiry_days       = max(0, expiry_days)
    policy.max_attempts      = max_attempts
    policy.lockout_minutes   = lockout_minutes
    db.commit()

    log_security_event(db, "policy_changed", actor=actor,
                       details=(
                           f"min={min_length}, upper={require_uppercase}, lower={require_lowercase}, "
                           f"digits={require_digits}, special={require_special}, "
                           f"history={history_count}, expiry={expiry_days}d, "
                           f"max_attempts={max_attempts}, lockout={lockout_minutes}min"
                       ))
    return RedirectResponse(url="/admin/password-policy?msg=salvo", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# Log de segurança / auditoria (admin)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/security-log", response_class=HTMLResponse)
def admin_security_log(
    request:    Request,
    event_type: str = Query("all"),
    username:   str = Query(""),
    page:       int = Query(1),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    from app.models import SecurityLog

    PAGE_SIZE = 50
    q = db.query(SecurityLog)
    if event_type != "all":
        q = q.filter(SecurityLog.event_type == event_type)
    if username.strip():
        q = q.filter(SecurityLog.username.ilike(f"%{username.strip()}%"))

    total  = q.count()
    offset = (page - 1) * PAGE_SIZE
    logs   = q.order_by(SecurityLog.created_at.desc()).offset(offset).limit(PAGE_SIZE).all()
    pages  = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    # Tipos distintos para o filtro
    all_types = [
        r[0] for r in
        db.query(SecurityLog.event_type).distinct().order_by(SecurityLog.event_type).all()
    ]

    return templates.TemplateResponse("auth/security_log.html", {
        "request":    request,
        "logs":       logs,
        "total":      total,
        "page":       page,
        "pages":      pages,
        "event_type": event_type,
        "username":   username,
        "all_types":  all_types,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Download da documentação técnica (admin only)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/documentation", dependencies=[Depends(require_admin)])
def download_documentation(request: Request):
    """Serve ARCHITECTURE.md como download para administradores."""
    doc_path = Path(__file__).parent.parent.parent / "ARCHITECTURE.md"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="Documentação não encontrada.")
    return FileResponse(
        path=str(doc_path),
        media_type="text/markdown",
        filename="HelloIT-Veeam-Reporter-Arquitetura.md",
    )
