"""
branding_service.py — Gerencia perfis de identidade visual.

Cadeia de fallback:
  perfil do cliente → perfil global padrão → defaults HelloIT hard-coded

O cache em memória evita consultas ao banco a cada requisição de CSS/página.
Invalidar com invalidate_cache() após salvar qualquer perfil.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Optional

# ── Defaults HelloIT ─────────────────────────────────────────────────────────

BASE_DEFAULTS: dict = {
    "id": None,
    "name": "HelloIT (padrão)",
    "is_default": True,
    "is_base": True,
    "client_id": None,
    # Informações gerais
    "system_name":   "Veeam Reporter",
    "short_name":    "HelloIT",
    "company_name":  "HelloIT",
    "header_text":   "HelloIT",
    "footer_text":   "Infraestrutura, Redes e Cibersegurança | helloit.com.br",
    "website_url":   "https://helloit.com.br",
    "contact_email": "",
    # Cores
    "primary_color":        "#7C3AED",
    "secondary_color":      "#101424",
    "accent_color":         "#A78BFA",
    "success_color":        "#2E7D32",
    "warning_color":        "#EDB458",
    "danger_color":         "#C62828",
    "background_color":     "#F7F9FC",
    "card_bg_color":        "#FFFFFF",
    "table_header_color":   "#7C3AED",
    "text_primary_color":   "#2D3748",
    "text_secondary_color": "#718096",
    # Arquivos
    "logo_main_path":        None,
    "logo_login_path":       None,
    "logo_report_path":      None,
    "favicon_path":          None,
    "pptx_cover_path":       None,
    "pptx_final_slide_path": None,
}

# ── Cache ─────────────────────────────────────────────────────────────────────

_lock: threading.Lock = threading.Lock()
_cache: dict[str, dict] = {}   # "global" | "client:{id}"


def invalidate_cache() -> None:
    """Limpa o cache após salvar alterações de branding."""
    with _lock:
        _cache.clear()


# ── Conversores ───────────────────────────────────────────────────────────────

def _profile_to_dict(profile) -> dict:
    """Converte ORM BrandingProfile → dict, aplicando fallback para cada campo."""
    d: dict = dict(BASE_DEFAULTS)   # começa com defaults
    d.update({
        "id":          profile.id,
        "name":        profile.name,
        "is_default":  profile.is_default,
        "is_base": profile.is_base,
        "client_id":   profile.client_id,
    })
    # Sobrescreve só os campos que foram preenchidos no banco
    text_fields = [
        "system_name", "short_name", "company_name", "header_text",
        "footer_text", "website_url", "contact_email",
    ]
    color_fields = [
        "primary_color", "secondary_color", "accent_color",
        "success_color", "warning_color", "danger_color",
        "background_color", "card_bg_color", "table_header_color",
        "text_primary_color", "text_secondary_color",
    ]
    file_fields = [
        "logo_main_path", "logo_login_path", "logo_report_path",
        "favicon_path", "pptx_cover_path", "pptx_final_slide_path",
    ]
    for f in text_fields + color_fields + file_fields:
        val = getattr(profile, f, None)
        if val is not None:
            d[f] = val
    return d


def hex_to_rgb_tuple(hex_color: str) -> tuple[int, int, int]:
    """Converte '#7C3AED' → (124, 58, 237)."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def validate_hex(color: str) -> bool:
    return bool(re.match(r"^#[0-9A-Fa-f]{6}$", color))


# ── Leitura ───────────────────────────────────────────────────────────────────

def _fetch_global(db) -> dict:
    from app.models import BrandingProfile
    profile = (
        db.query(BrandingProfile)
        .filter(BrandingProfile.client_id.is_(None))
        .filter(BrandingProfile.is_default.is_(True))
        .first()
    )
    if profile is None:
        return dict(BASE_DEFAULTS)
    return _profile_to_dict(profile)


def _fetch_client(db, client_id: int) -> dict:
    from app.models import BrandingProfile
    profile = (
        db.query(BrandingProfile)
        .filter(BrandingProfile.client_id == client_id)
        .first()
    )
    if profile is None:
        return _fetch_global(db)
    # Merge: client overrides global for non-None fields
    base = _fetch_global(db)
    override = _profile_to_dict(profile)
    merged = dict(base)
    # Color + text fields: use client value if it differs from HelloIT default
    for key in override:
        if override[key] is not None and key not in ("id", "name", "is_default", "is_base", "client_id"):
            if override[key] != BASE_DEFAULTS.get(key):
                merged[key] = override[key]
    merged["id"] = override["id"]
    merged["client_id"] = client_id
    return merged


def get_branding(client_id: Optional[int] = None) -> dict:
    """
    Retorna o branding ativo para o cliente (ou global se client_id=None).
    Usa cache em memória com invalidação explícita.
    """
    cache_key = f"client:{client_id}" if client_id is not None else "global"

    with _lock:
        if cache_key in _cache:
            return _cache[cache_key]

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if client_id is not None:
            result = _fetch_client(db, client_id)
        else:
            result = _fetch_global(db)
    finally:
        db.close()

    with _lock:
        _cache[cache_key] = result

    return result


def get_logo_url(path: Optional[str]) -> Optional[str]:
    """Converte o path relativo de branding em URL pública."""
    if not path:
        return None
    # Garante que não há path traversal
    safe = path.replace("\\", "/").lstrip("/")
    if ".." in safe:
        return None
    return f"/branding/file/{safe}"


# ── CSS dinâmico ──────────────────────────────────────────────────────────────

def build_css(branding: dict) -> str:
    """Gera o bloco :root com as CSS variables de branding."""
    b = branding
    logo_url = get_logo_url(b.get("logo_main_path"))
    login_logo_url = get_logo_url(b.get("logo_login_path"))
    logo_css = f"url('{logo_url}')" if logo_url else "none"
    login_logo_css = f"url('{login_logo_url}')" if login_logo_url else "none"

    return f""":root {{
  --brand-primary:        {b['primary_color']};
  --brand-secondary:      {b['secondary_color']};
  --brand-accent:         {b['accent_color']};
  --brand-success:        {b['success_color']};
  --brand-warning:        {b['warning_color']};
  --brand-danger:         {b['danger_color']};
  --brand-bg:             {b['background_color']};
  --brand-card-bg:        {b['card_bg_color']};
  --brand-table-header:   {b['table_header_color']};
  --brand-text-primary:   {b['text_primary_color']};
  --brand-text-secondary: {b['text_secondary_color']};
  --brand-logo:           {logo_css};
  --brand-login-logo:     {login_logo_css};
}}
"""


# ── Scaffold de dados ─────────────────────────────────────────────────────────

def get_all_profiles(db) -> list[dict]:
    """Lista todos os perfis de branding (global + por cliente)."""
    from app.models import BrandingProfile
    profiles = db.query(BrandingProfile).order_by(
        BrandingProfile.is_default.desc(), BrandingProfile.name
    ).all()
    return [_profile_to_dict(p) for p in profiles]


def get_profile_by_id(db, profile_id: int) -> Optional[dict]:
    from app.models import BrandingProfile
    p = db.query(BrandingProfile).get(profile_id)
    if p is None:
        return None
    return _profile_to_dict(p)


def update_profile(db, profile_id: int, data: dict, actor: str) -> dict:
    """Atualiza um perfil existente. Invalida o cache ao final."""
    from app.models import BrandingProfile

    profile = db.query(BrandingProfile).get(profile_id)
    if profile is None:
        raise ValueError(f"Perfil {profile_id} não encontrado")

    updatable = [
        "name", "system_name", "short_name", "company_name",
        "header_text", "footer_text", "website_url", "contact_email",
        "primary_color", "secondary_color", "accent_color",
        "success_color", "warning_color", "danger_color",
        "background_color", "card_bg_color", "table_header_color",
        "text_primary_color", "text_secondary_color",
    ]
    for field in updatable:
        if field in data:
            val = data[field]
            # Valida cores
            if field.endswith("_color") and val and not validate_hex(val):
                raise ValueError(f"Cor inválida para '{field}': {val}")
            setattr(profile, field, val or None)

    profile.updated_at = datetime.utcnow()
    profile.updated_by = actor
    db.commit()
    db.refresh(profile)
    invalidate_cache()
    return _profile_to_dict(profile)


def reset_to_base(db, profile_id: int, actor: str) -> dict:
    """Restaura um perfil para os padrões HelloIT."""
    from app.models import BrandingProfile

    profile = db.query(BrandingProfile).get(profile_id)
    if profile is None:
        raise ValueError(f"Perfil {profile_id} não encontrado")

    sd = BASE_DEFAULTS
    profile.system_name   = sd["system_name"]
    profile.short_name    = sd["short_name"]
    profile.company_name  = sd["company_name"]
    profile.header_text   = sd["header_text"]
    profile.footer_text   = sd["footer_text"]
    profile.website_url   = sd["website_url"]
    profile.contact_email = ""
    profile.primary_color        = sd["primary_color"]
    profile.secondary_color      = sd["secondary_color"]
    profile.accent_color         = sd["accent_color"]
    profile.success_color        = sd["success_color"]
    profile.warning_color        = sd["warning_color"]
    profile.danger_color         = sd["danger_color"]
    profile.background_color     = sd["background_color"]
    profile.card_bg_color        = sd["card_bg_color"]
    profile.table_header_color   = sd["table_header_color"]
    profile.text_primary_color   = sd["text_primary_color"]
    profile.text_secondary_color = sd["text_secondary_color"]
    # Preserva logos/arquivos — usuário pode querer manter
    profile.updated_at = datetime.utcnow()
    profile.updated_by = actor
    db.commit()
    db.refresh(profile)
    invalidate_cache()
    return _profile_to_dict(profile)


def update_logo_path(db, profile_id: int, field: str, path: Optional[str], actor: str) -> None:
    """Atualiza o path de um arquivo de branding (logo, pptx, etc.)."""
    from app.models import BrandingProfile
    allowed_fields = {
        "logo_main_path", "logo_login_path", "logo_report_path",
        "favicon_path", "pptx_cover_path", "pptx_final_slide_path",
    }
    if field not in allowed_fields:
        raise ValueError(f"Campo inválido: {field}")

    profile = db.query(BrandingProfile).get(profile_id)
    if profile is None:
        raise ValueError(f"Perfil {profile_id} não encontrado")

    setattr(profile, field, path)
    profile.updated_at = datetime.utcnow()
    profile.updated_by = actor
    db.commit()
    invalidate_cache()
