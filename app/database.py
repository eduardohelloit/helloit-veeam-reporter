import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://veeam@localhost:5432/veeam_reporter",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Aplica todas as migrações Alembic pendentes e garante dados iniciais.
    Chamado no startup da aplicação via @app.on_event("startup").
    """
    from alembic.config import Config
    from alembic import command
    import pathlib

    alembic_cfg = Config(str(pathlib.Path(__file__).parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    alembic_cfg.set_main_option(
        "script_location",
        str(pathlib.Path(__file__).parent.parent / "alembic"),
    )

    command.upgrade(alembic_cfg, "head")
    print("[DB] Migrações Alembic aplicadas.")

    _ensure_password_policy()
    _ensure_default_user()
    _ensure_default_branding()
    _ensure_default_offenders()
    _ensure_default_rpo_policies()
    _ensure_default_offload_categories()


def _ensure_password_policy():
    from app.models import PasswordPolicy
    db = SessionLocal()
    try:
        if db.query(PasswordPolicy).count() == 0:
            db.add(PasswordPolicy())
            db.commit()
            print("[AUTH] Política de senha padrão criada.")
    finally:
        db.close()


def _ensure_default_branding():
    from app.models import BrandingProfile
    from app.services.branding_service import BASE_DEFAULTS
    from datetime import datetime

    db = SessionLocal()
    try:
        existing = (
            db.query(BrandingProfile)
            .filter(BrandingProfile.client_id.is_(None))
            .filter(BrandingProfile.is_default.is_(True))
            .first()
        )
        if existing:
            return

        sd = BASE_DEFAULTS
        profile = BrandingProfile(
            name          = "HelloIT (padrão)",
            is_default    = True,
            is_base   = True,
            client_id     = None,
            system_name   = sd["system_name"],
            short_name    = sd["short_name"],
            company_name  = sd["company_name"],
            header_text   = sd["header_text"],
            footer_text   = sd["footer_text"],
            website_url   = sd["website_url"],
            contact_email = "",
            primary_color        = sd["primary_color"],
            secondary_color      = sd["secondary_color"],
            accent_color         = sd["accent_color"],
            success_color        = sd["success_color"],
            warning_color        = sd["warning_color"],
            danger_color         = sd["danger_color"],
            background_color     = sd["background_color"],
            card_bg_color        = sd["card_bg_color"],
            table_header_color   = sd["table_header_color"],
            text_primary_color   = sd["text_primary_color"],
            text_secondary_color = sd["text_secondary_color"],
            created_at    = datetime.utcnow(),
            created_by    = "sistema",
        )
        db.add(profile)
        db.commit()
        print("[BRANDING] Perfil HelloIT padrão criado.")
    finally:
        db.close()


def _ensure_default_user():
    from app.models import User
    from app.auth import hash_password

    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            existing_admin = db.query(User).filter(User.username == "admin").first()
            if existing_admin and not existing_admin.is_admin:
                existing_admin.is_admin = True
                db.commit()
                print("[AUTH] Usuário admin existente promovido a administrador.")
            return

        username     = os.environ.get("ADMIN_USERNAME", "admin")
        password     = os.environ.get("ADMIN_PASSWORD", "")
        email        = os.environ.get("ADMIN_EMAIL", "")
        force_change = False

        if not password:
            import secrets
            password = secrets.token_urlsafe(12)
            force_change = True
            print(f"[AUTH] ADMIN_PASSWORD não definido. Senha temporária gerada: {password}", flush=True)
            print("[AUTH] Troca de senha obrigatória no primeiro acesso.", flush=True)

        admin = User(
            username=username,
            email=email or None,
            full_name="Administrador",
            password_hash=hash_password(password),
            is_active=True,
            is_admin=True,
            must_change_password=force_change,
            created_by="sistema",
        )
        db.add(admin)
        db.commit()
        print(f"[AUTH] Usuário administrador '{username}' criado.")
    finally:
        db.close()


def _ensure_default_offenders() -> None:
    """Semeia as categorias e regras de ofensores pré-definidas, se não existirem."""
    from app.models import OffenderCategory, OffenderRule
    from app.services.offender_service import DEFAULT_CATEGORIES

    db = SessionLocal()
    try:
        if db.query(OffenderCategory).count() > 0:
            return  # Já existe, não sobrescreve

        now = __import__("datetime").datetime.utcnow()
        for cat_def in DEFAULT_CATEGORIES:
            cat = OffenderCategory(
                name        = cat_def["name"],
                description = cat_def.get("description"),
                severity    = cat_def.get("severity", "medium"),
                color       = cat_def.get("color"),
                priority    = cat_def.get("priority", 100),
                is_active   = True,
                created_at  = now,
            )
            db.add(cat)
            db.flush()  # obter cat.id

            for pattern in cat_def.get("rules", []):
                rule = OffenderRule(
                    category_id    = cat.id,
                    pattern        = pattern,
                    match_type     = "contains",
                    case_sensitive = False,
                    is_active      = True,
                    priority       = 100,
                    created_at     = now,
                )
                db.add(rule)

        db.commit()
        total_cats  = db.query(OffenderCategory).count()
        total_rules = db.query(OffenderRule).count()
        print(f"[OFFENDER] {total_cats} categorias e {total_rules} regras de ofensores criadas.")
    except Exception as e:
        db.rollback()
        print(f"[OFFENDER] Erro ao semear ofensores: {e}")
    finally:
        db.close()


def _ensure_default_offload_categories() -> None:
    """Semeia as categorias e regras de classificação de motivo de offload."""
    from app.models import OffloadReasonCategory, OffloadReasonRule
    from app.services.offload_service import DEFAULT_OFFLOAD_CATEGORIES

    db = SessionLocal()
    try:
        if db.query(OffloadReasonCategory).count() > 0:
            return  # já existe, não sobrescreve

        now = __import__("datetime").datetime.utcnow()
        for cat_def in DEFAULT_OFFLOAD_CATEGORIES:
            cat = OffloadReasonCategory(
                name        = cat_def["name"],
                description = cat_def.get("description"),
                severity    = cat_def.get("severity", "medium"),
                color       = cat_def.get("color"),
                priority    = cat_def.get("priority", 100),
                is_active   = True,
                created_at  = now,
            )
            db.add(cat)
            db.flush()  # obter cat.id

            for pattern in cat_def.get("rules", []):
                db.add(OffloadReasonRule(
                    category_id    = cat.id,
                    pattern        = pattern,
                    match_type     = "contains",
                    case_sensitive = False,
                    is_active      = True,
                    priority       = 100,
                    created_at     = now,
                ))

        db.commit()
        total_cats  = db.query(OffloadReasonCategory).count()
        total_rules = db.query(OffloadReasonRule).count()
        print(f"[OFFLOAD] {total_cats} categorias e {total_rules} regras de motivo criadas.")
    except Exception as e:
        db.rollback()
        print(f"[OFFLOAD] Erro ao semear categorias de motivo: {e}")
    finally:
        db.close()


def _ensure_default_rpo_policies() -> None:
    """Semeia políticas de RPO padrão se a tabela estiver vazia."""
    from app.models import RpoPolicy

    db = SessionLocal()
    try:
        if db.query(RpoPolicy).count() > 0:
            return

        now = __import__("datetime").datetime.utcnow()
        defaults = [
            ("RPO 30min",  30,   "critical"),
            ("RPO 1h",     60,   "critical"),
            ("RPO 2h",     120,  "high"),
            ("RPO 4h",     240,  "high"),
            ("RPO 5h",     300,  "medium"),
            ("RPO 8h",     480,  "medium"),
            ("RPO 12h",    720,  "medium"),
            ("RPO 24h",    1440, "low"),
            ("RPO 48h",    2880, "low"),
        ]
        for name, minutes, severity in defaults:
            db.add(RpoPolicy(
                name             = name,
                description      = f"RPO esperado de {name.replace('RPO ', '')}",
                expected_minutes = minutes,
                severity_default = severity,
                is_active        = True,
                created_at       = now,
            ))

        db.commit()
        total = db.query(RpoPolicy).count()
        print(f"[RPO] {total} políticas de RPO padrão criadas.")
    except Exception as e:
        db.rollback()
        print(f"[RPO] Erro ao semear políticas: {e}")
    finally:
        db.close()
