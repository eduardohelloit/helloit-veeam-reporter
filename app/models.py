"""
SQLAlchemy models — HelloIT Veeam Reporter

Princípio arquitetural:
  Upload  = carga de dados → alimenta ImportedEvent (base histórica)
  Relatório = consulta por cliente + período → gera ReportSession + JobSummary

Fluxo:
  UploadSession → (parse + dedup) → ImportedEvent
  ReportSession → (query ImportedEvent por client_id + período) → JobSummary
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ── Tenant ────────────────────────────────────────────────────────────────────

class Client(Base):
    __tablename__ = "clients"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:        Mapped[str]           = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)


class UserClient(Base):
    """
    Vínculo de escopo (multi-tenant): quais clientes um usuário NÃO-admin pode ver.
    Admin enxerga todos os clientes independentemente desta tabela.
    """
    __tablename__ = "user_clients"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_user_client"),
        Index("idx_user_clients_user", "user_id"),
    )

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:    Mapped[int]      = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id:  Mapped[int]      = mapped_column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


# ── Autenticação ──────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id:                   Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    username:             Mapped[str]           = mapped_column(String(100), unique=True, nullable=False)
    email:                Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name:            Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_hash:        Mapped[str]           = mapped_column(String(255), nullable=False)
    is_active:            Mapped[bool]          = mapped_column(Boolean, default=True)
    is_admin:             Mapped[bool]          = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool]          = mapped_column(Boolean, default=False)
    failed_attempts:      Mapped[int]           = mapped_column(Integer, default=0)
    locked_until:         Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:           Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    created_by:           Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_login:           Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_password_change: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    password_expires_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    password_history: Mapped[list["PasswordHistory"]] = relationship(
        "PasswordHistory", back_populates="user",
        cascade="all, delete-orphan",
        order_by="PasswordHistory.changed_at.desc()",
    )


class PasswordPolicy(Base):
    """Singleton (id=1) — use get_policy() para ler."""
    __tablename__ = "password_policy"

    id:                Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    min_length:        Mapped[int]  = mapped_column(Integer, default=8)
    require_uppercase: Mapped[bool] = mapped_column(Boolean, default=True)
    require_lowercase: Mapped[bool] = mapped_column(Boolean, default=True)
    require_digits:    Mapped[bool] = mapped_column(Boolean, default=True)
    require_special:   Mapped[bool] = mapped_column(Boolean, default=False)
    history_count:     Mapped[int]  = mapped_column(Integer, default=5)
    expiry_days:       Mapped[int]  = mapped_column(Integer, default=0)
    max_attempts:      Mapped[int]  = mapped_column(Integer, default=5)
    lockout_minutes:   Mapped[int]  = mapped_column(Integer, default=15)


class PasswordHistory(Base):
    __tablename__ = "password_history"

    id:            Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:       Mapped[int]      = mapped_column(Integer, ForeignKey("users.id"))
    password_hash: Mapped[str]      = mapped_column(String(255), nullable=False)
    changed_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="password_history")


class SecurityLog(Base):
    """Log de auditoria append-only para eventos de autenticação e administração."""
    __tablename__ = "security_log"

    id:         Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str]           = mapped_column(String(50), nullable=False)
    username:   Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    actor:      Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    details:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)


# ── Cargas de dados (Upload) ───────────────────────────────────────────────────

class UploadSession(Base):
    """
    Representa uma carga de dados (importação de arquivo Event Viewer).
    NÃO gera relatório automaticamente. Os eventos importados ficam na base
    histórica (ImportedEvent) disponíveis para qualquer consulta posterior.
    """
    __tablename__ = "upload_sessions"

    id:                Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:         Mapped[int]      = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    original_filename: Mapped[str]      = mapped_column(String(255), nullable=False)
    stored_filename:   Mapped[str]      = mapped_column(String(255), nullable=False)
    file_format:       Mapped[str]      = mapped_column(String(10), nullable=False)  # xml | evtx | csv
    uploaded_by:       Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    uploaded_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Status da carga
    status:         Mapped[str]           = mapped_column(String(20), default="processing")
    status_message: Mapped[str]           = mapped_column(Text, default="Iniciando...")
    error_message:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Período detectado automaticamente nos eventos do arquivo
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    period_end:   Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Estatísticas da carga
    total_events_read:        Mapped[int] = mapped_column(Integer, default=0)
    new_events_inserted:      Mapped[int] = mapped_column(Integer, default=0)
    duplicate_events_skipped: Mapped[int] = mapped_column(Integer, default=0)
    invalid_events_skipped:   Mapped[int] = mapped_column(Integer, default=0)
    backup_events_count:      Mapped[int] = mapped_column(Integer, default=0)
    audit_events_count:       Mapped[int] = mapped_column(Integer, default=0)

    client: Mapped["Client"] = relationship("Client")
    events: Mapped[list["ImportedEvent"]] = relationship(
        "ImportedEvent", back_populates="upload_session", cascade="all, delete-orphan"
    )


# ── Eventos importados (base histórica unificada) ──────────────────────────────

class ImportedEvent(Base):
    """
    Base histórica unificada de eventos Veeam.
    Armazena tanto eventos de backup quanto eventos de auditoria em uma única tabela,
    diferenciados pelo campo event_category.

    Campos raw_*: nunca sobrescritos — base para reprocessamento futuro.
    Campos derivados: recalculáveis a partir dos dados brutos.

    Constraint UNIQUE(client_id, content_hash) garante deduplicação automática
    mesmo em uploads com períodos sobrepostos.
    """
    __tablename__ = "imported_events"
    __table_args__ = (
        UniqueConstraint("client_id", "content_hash", name="uq_events_client_hash"),
        # ── Índices simples (2 colunas) ───────────────────────────────────────
        Index("idx_ev_client_time",     "client_id", "time_created"),
        Index("idx_ev_client_cat",      "client_id", "event_category"),
        Index("idx_ev_client_job",      "client_id", "job_name_normalized"),
        Index("idx_ev_client_result",   "client_id", "job_result"),
        Index("idx_ev_event_id",        "event_id"),
        Index("idx_ev_upload_session",  "upload_session_id"),
        Index("idx_ev_vbr_hostname",    "vbr_hostname"),
        # ── Índices compostos (3 colunas) para queries de dashboard ──────────
        # Cobrem filtros WHERE client_id=X AND time_created BETWEEN ... sem scan
        Index("idx_ev_client_eid_time",    "client_id", "event_id",            "time_created"),
        Index("idx_ev_client_cat_time",    "client_id", "event_category",      "time_created"),
        Index("idx_ev_client_job_time",    "client_id", "job_name_normalized",  "time_created"),
        Index("idx_ev_client_result_time", "client_id", "job_result",           "time_created"),
        Index("idx_ev_client_audit_time",  "client_id", "audit_event_type",     "time_created"),
    )

    id:                Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_session_id: Mapped[int] = mapped_column(Integer, ForeignKey("upload_sessions.id"), nullable=False)
    client_id:         Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)

    # ── Chave de deduplicação ─────────────────────────────────────────────────
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ── Dados brutos (nunca sobrescritos) ─────────────────────────────────────
    raw_event_xml:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # EVTX / XML
    raw_event_json:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # CSV
    original_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_name:    Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    channel:          Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    record_id:        Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_filename:  Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    imported_at:      Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)

    # ── Campos indexados (maioria brutos, rápidos para consulta) ─────────────
    event_id:     Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    time_created: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    vbr_hostname: Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)

    # ── Campos derivados (recalculáveis via reprocessamento) ─────────────────
    event_category:      Mapped[str]           = mapped_column(String(20), default="backup")
    # 'backup' | 'log_backup' | 'audit'

    job_name:            Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    job_name_normalized: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    job_type:            Mapped[str]           = mapped_column(String(20), default="backup")
    # 'backup' | 'log_backup'  (equivale a event_category para eventos não-audit)

    job_result:          Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 0=Sucesso  1=Warning  2=Falha

    will_be_retried: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Campos de auditoria (apenas para event_category='audit')
    operator:          Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    audit_event_type:  Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    audit_event_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    audit_details:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Rastreabilidade do parser
    parser_version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    parsed_at:      Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)

    # ── Classificação de ofensores (derivado — reprocessável) ─────────────────
    offender_category_id:   Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    offender_rule_id:       Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    offender_match_pattern: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    offender_classified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    upload_session: Mapped["UploadSession"] = relationship("UploadSession", back_populates="events")


# ── Relatórios por período ────────────────────────────────────────────────────

class ReportSession(Base):
    """
    Consulta histórica por cliente + período.
    NÃO tem vínculo com UploadSession — o relatório é gerado a partir de
    todos os ImportedEvents do cliente no intervalo selecionado.
    """
    __tablename__ = "report_sessions"

    id:                 Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:          Mapped[int]      = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    client_name:        Mapped[str]      = mapped_column(String(255), default="")
    date_start:         Mapped[datetime] = mapped_column(DateTime, nullable=False)
    date_end:           Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by:         Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at:         Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    include_backup:     Mapped[bool]     = mapped_column(Boolean, default=True)
    include_log_backup: Mapped[bool]     = mapped_column(Boolean, default=True)
    show_all_jobs:      Mapped[bool]     = mapped_column(Boolean, default=False)
    is_snapshot:        Mapped[bool]     = mapped_column(Boolean, default=False)

    job_summaries: Mapped[list["JobSummary"]] = relationship(
        "JobSummary", back_populates="report", cascade="all, delete-orphan"
    )


class JobSummary(Base):
    """Resumo por job calculado a partir de uma consulta histórica (ReportSession)."""
    __tablename__ = "job_summaries"

    id:                   Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id:            Mapped[int] = mapped_column(Integer, ForeignKey("report_sessions.id"), nullable=False)
    job_name:             Mapped[str] = mapped_column(String(500), nullable=False)
    job_name_normalized:  Mapped[str] = mapped_column(String(500), default="")
    job_type:             Mapped[str] = mapped_column(String(20), default="backup")
    total_executions:     Mapped[int] = mapped_column(Integer, default=0)
    failed_count:         Mapped[int] = mapped_column(Integer, default=0)
    warning_count:        Mapped[int] = mapped_column(Integer, default=0)
    success_count:        Mapped[int] = mapped_column(Integer, default=0)
    last3_summary:        Mapped[str] = mapped_column(String(50), default="Sem histórico suficiente")
    situation:            Mapped[str] = mapped_column(String(50), default="Indefinido")
    last_error_message:   Mapped[str] = mapped_column(Text, default="")
    action_ongoing:       Mapped[str] = mapped_column(Text, default="")
    technical_observation:Mapped[str] = mapped_column(Text, default="")
    responsible:          Mapped[str] = mapped_column(String(255), default="")
    ticket:               Mapped[str] = mapped_column(String(255), default="")

    report: Mapped["ReportSession"] = relationship("ReportSession", back_populates="job_summaries")


class BrandingProfile(Base):
    """
    Perfil de identidade visual do sistema.
    Linha com client_id=NULL e is_default=True  →  perfil global padrão.
    Linha com client_id=X                        →  override por cliente.
    """
    __tablename__ = "branding_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="HelloIT (padrão)")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_base: Mapped[bool] = mapped_column(Boolean, default=False)   # perfil base imutável
    client_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("clients.id"), nullable=True)

    # ── Informações gerais ───────────────────────────────────────────────
    system_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    short_name:  Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    company_name:Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    header_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    footer_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ── Cores ────────────────────────────────────────────────────────────
    primary_color:        Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    secondary_color:      Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    accent_color:         Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    success_color:        Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    warning_color:        Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    danger_color:         Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    background_color:     Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    card_bg_color:        Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    table_header_color:   Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    text_primary_color:   Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    text_secondary_color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)

    # ── Arquivos / logotipos ─────────────────────────────────────────────
    logo_main_path:        Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    logo_login_path:       Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    logo_report_path:      Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    favicon_path:          Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pptx_cover_path:       Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pptx_final_slide_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ── Auditoria ────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    client: Mapped[Optional["Client"]] = relationship("Client", foreign_keys=[client_id])


class JobAction(Base):
    """
    Ações e anotações persistentes por (cliente, job normalizado).
    Sobrevive entre relatórios — quando um novo relatório é gerado, os campos
    desta tabela pré-populam os JobSummary correspondentes.
    """
    __tablename__ = "job_actions"
    __table_args__ = (
        UniqueConstraint("client_id", "job_name_normalized", name="uq_job_actions_client_job"),
    )

    id:                    Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)  # noqa: E501
    client_id:             Mapped[int]           = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    job_name_normalized:   Mapped[str]           = mapped_column(String(500), nullable=False)
    situation:             Mapped[str]           = mapped_column(String(50), default="Indefinido")
    action_ongoing:        Mapped[str]           = mapped_column(Text, default="")
    technical_observation: Mapped[str]           = mapped_column(Text, default="")
    responsible:           Mapped[str]           = mapped_column(String(255), default="")
    ticket:                Mapped[str]           = mapped_column(String(255), default="")
    updated_at:            Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_by:            Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


# ── Classificação automática de ofensores de backup ──────────────────────────

class OffenderCategory(Base):
    """
    Categoria de ofensor (ex: 'VSS', 'Repositório / Desempenho').
    Agrupa regras de classificação por padrão de string nas mensagens de erro.
    """
    __tablename__ = "offender_categories"
    __table_args__ = (
        Index("idx_offcat_active_prio", "is_active", "priority"),
    )

    id:          Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:        Mapped[str]            = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity:    Mapped[str]            = mapped_column(String(20), default="medium")
    # low | medium | high | critical
    color:       Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, default=True)
    priority:    Mapped[int]            = mapped_column(Integer, default=100)
    # Maior = verificado primeiro. Regras mais específicas devem ter prioridade maior.
    notes:       Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    rules: Mapped[list["OffenderRule"]] = relationship(
        "OffenderRule", back_populates="category",
        cascade="all, delete-orphan",
        order_by="OffenderRule.priority.desc(), OffenderRule.id",
    )


class OffenderRule(Base):
    """
    Regra de classificação de ofensor.
    Uma regra contém um padrão de string e um tipo de comparação.
    """
    __tablename__ = "offender_rules"
    __table_args__ = (
        Index("idx_offrule_category_active", "category_id", "is_active"),
    )

    id:             Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id:    Mapped[int]            = mapped_column(
        Integer, ForeignKey("offender_categories.id", ondelete="CASCADE"), nullable=False
    )
    pattern:        Mapped[str]            = mapped_column(String(500), nullable=False)
    match_type:     Mapped[str]            = mapped_column(String(20), default="contains")
    # contains | starts_with | ends_with | regex
    case_sensitive: Mapped[bool]          = mapped_column(Boolean, default=False)
    is_active:      Mapped[bool]          = mapped_column(Boolean, default=True)
    priority:       Mapped[int]            = mapped_column(Integer, default=100)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    category: Mapped["OffenderCategory"] = relationship("OffenderCategory", back_populates="rules")


# ── Políticas de RPO esperado por VM ─────────────────────────────────────────

class RpoPolicy(Base):
    """
    Política de RPO nomeada e reutilizável (ex: 'RPO 2h', 'RPO 24h').
    Pode ser vinculada a múltiplas VMs via VmRpoAssignment.
    """
    __tablename__ = "rpo_policies"
    __table_args__ = (
        Index("idx_rpopol_active", "is_active"),
    )

    id:               Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:             Mapped[str]            = mapped_column(String(100), nullable=False, unique=True)
    description:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_minutes: Mapped[int]            = mapped_column(Integer, nullable=False)
    severity_default: Mapped[str]            = mapped_column(String(20), default="medium")
    is_active:        Mapped[bool]          = mapped_column(Boolean, default=True)
    created_at:       Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    assignments: Mapped[list["VmRpoAssignment"]] = relationship(
        "VmRpoAssignment", back_populates="policy"
    )


class VmRpoAssignment(Base):
    """
    Vincula uma VM a um RPO esperado (via política ou valor direto).
    Uma VM só pode ter uma atribuição ativa por cliente.
    """
    __tablename__ = "vm_rpo_assignments"
    __table_args__ = (
        Index("idx_vmrpo_client_vm", "client_id", "vm_name"),
        UniqueConstraint("client_id", "vm_name", name="uq_vmrpo_client_vm"),
    )

    id:                  Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:           Mapped[int]            = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    vm_name:             Mapped[str]            = mapped_column(String(500), nullable=False)
    vm_name_normalized:  Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    rpo_policy_id:       Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("rpo_policies.id", ondelete="SET NULL"), nullable=True
    )
    expected_minutes:    Mapped[int]            = mapped_column(Integer, nullable=False)
    # Copiado da política para permitir ajuste local; pode divergir após edição da política.
    criticality:         Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notes:               Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active:           Mapped[bool]          = mapped_column(Boolean, default=True)
    created_by:          Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_by:          Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at:          Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:          Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    client: Mapped["Client"]              = relationship("Client")
    policy: Mapped[Optional["RpoPolicy"]] = relationship("RpoPolicy", back_populates="assignments")


# ── Offload / Capacity Tier (SOBR) ───────────────────────────────────────────
#
# Filosofia idêntica ao restante do sistema:
#   Carga (OffloadImport) = ingestão de arquivo NDJSON → alimenta a base histórica
#   OffloadSession        = uma execução/tentativa de offload (fato)
#   OffloadJob            = a rotina (dimensão), identificada por client_id +
#                           job_name_normalized (NÃO pelo job_id do arquivo).
#
# Decisão de modelagem:
#   job_name_normalized identifica a ROTINA.
#   session_id          identifica a EXECUÇÃO.
#   job_id do arquivo   é apenas dado de origem → armazenado em source_job_id,
#                       nunca usado para agrupar execuções.

class OffloadImport(Base):
    """Representa cada carga/importação de um arquivo NDJSON de offload."""
    __tablename__ = "offload_imports"
    __table_args__ = (
        Index("idx_offimp_client_time", "client_id", "imported_at"),
        Index("idx_offimp_file_hash",   "file_hash"),
        Index("idx_offimp_status",      "status"),
    )

    id:                Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:         Mapped[int]           = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    filename:          Mapped[str]           = mapped_column(String(255), nullable=False)   # nome armazenado
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_hash:         Mapped[Optional[str]] = mapped_column(String(64), nullable=True)      # sha256 do arquivo
    file_size_bytes:   Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    status:         Mapped[str]           = mapped_column(String(20), default="processing")  # processing|completed|failed
    status_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    total_lines:        Mapped[int] = mapped_column(Integer, default=0)
    inserted_sessions:  Mapped[int] = mapped_column(Integer, default=0)
    duplicate_sessions: Mapped[int] = mapped_column(Integer, default=0)
    ignored_lines:      Mapped[int] = mapped_column(Integer, default=0)
    failed_lines:       Mapped[int] = mapped_column(Integer, default=0)
    success_count:      Mapped[int] = mapped_column(Integer, default=0)
    warning_count:      Mapped[int] = mapped_column(Integer, default=0)
    failed_count:       Mapped[int] = mapped_column(Integer, default=0)

    min_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    uploaded_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    imported_at: Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    created_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    client: Mapped["Client"] = relationship("Client")


class OffloadJob(Base):
    """
    Rotina de offload (dimensão). Unicidade por (client_id, job_name_normalized) —
    o job_id do arquivo NÃO identifica a rotina.
    """
    __tablename__ = "offload_jobs"
    __table_args__ = (
        UniqueConstraint("client_id", "job_name_normalized", name="uq_offjob_client_norm"),
        Index("idx_offjob_client",      "client_id"),
        Index("idx_offjob_norm",        "job_name_normalized"),
        Index("idx_offjob_client_norm", "client_id", "job_name_normalized"),
    )

    id:                  Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:           Mapped[int]      = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    job_name:            Mapped[str]      = mapped_column(String(500), nullable=False)
    job_name_normalized: Mapped[str]      = mapped_column(String(500), nullable=False)
    first_seen_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_seen_at:        Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:          Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:          Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class OffloadSession(Base):
    """
    Uma execução/tentativa de offload. Uma linha por execução.

    Deduplicação idempotente via dois índices únicos parciais:
      • UNIQUE(client_id, session_id)   WHERE session_id IS NOT NULL  (principal)
      • UNIQUE(client_id, content_hash) WHERE session_id IS NULL       (fallback)
    Importar o mesmo arquivo duas vezes não duplica.
    """
    __tablename__ = "offload_sessions"
    __table_args__ = (
        Index("uq_offsess_client_session", "client_id", "session_id",
              unique=True, postgresql_where=text("session_id IS NOT NULL")),
        Index("uq_offsess_client_hash", "client_id", "content_hash",
              unique=True, postgresql_where=text("session_id IS NULL")),
        Index("idx_offsess_job_time",    "client_id", "offload_job_id", "started_at"),
        Index("idx_offsess_result_time", "client_id", "result",         "started_at"),
        Index("idx_offsess_cat_time",    "client_id", "reason_category_id", "started_at"),
        Index("idx_offsess_client_time", "client_id", "started_at"),
        Index("idx_offsess_import",      "offload_import_id"),
    )

    id:                Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:         Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    offload_job_id:    Mapped[int] = mapped_column(Integer, ForeignKey("offload_jobs.id"), nullable=False)
    offload_import_id: Mapped[int] = mapped_column(Integer, ForeignKey("offload_imports.id"), nullable=False)

    # ── Identificação da execução (origem) ────────────────────────────────────
    session_id:        Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # dedup principal
    source_job_id:     Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # job_id bruto (NÃO agrupar)
    job_name_snapshot: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # nome no momento da execução

    # ── Dados da sessão ───────────────────────────────────────────────────────
    result:           Mapped[str]           = mapped_column(String(20), nullable=False)   # Success|Warning|Failed
    state:            Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    started_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at:         Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)

    # ── Motivo (bruto preservado + derivados reprocessáveis) ──────────────────
    reason_raw:         Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason_normalized:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason_category_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # FK em migration (SET NULL)
    reason_rule_id:     Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    matched_pattern:    Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    target_ip:          Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    target_port:        Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Dedup fallback + rastreabilidade ──────────────────────────────────────
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    source:       Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at:   Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:   Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class OffloadReasonCategory(Base):
    """Categoria de motivo de falha/aviso de offload (ex.: 'Rescan do SOBR necessário')."""
    __tablename__ = "offload_reason_categories"
    __table_args__ = (
        Index("idx_offreasoncat_active_prio", "is_active", "priority"),
    )

    id:          Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:        Mapped[str]            = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity:    Mapped[str]            = mapped_column(String(20), default="medium")
    color:       Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, default=True)
    priority:    Mapped[int]            = mapped_column(Integer, default=100)  # maior = avaliado primeiro
    created_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    rules: Mapped[list["OffloadReasonRule"]] = relationship(
        "OffloadReasonRule", back_populates="category",
        cascade="all, delete-orphan",
        order_by="OffloadReasonRule.priority.desc(), OffloadReasonRule.id",
    )


class OffloadReasonRule(Base):
    """Regra de classificação de motivo de offload (pattern matching)."""
    __tablename__ = "offload_reason_rules"
    __table_args__ = (
        Index("idx_offreasonrule_cat_active", "category_id", "is_active"),
    )

    id:             Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id:    Mapped[int]            = mapped_column(
        Integer, ForeignKey("offload_reason_categories.id", ondelete="CASCADE"), nullable=False
    )
    pattern:        Mapped[str]            = mapped_column(String(500), nullable=False)
    match_type:     Mapped[str]            = mapped_column(String(20), default="contains")  # contains|starts_with|ends_with|regex
    case_sensitive: Mapped[bool]          = mapped_column(Boolean, default=False)
    is_active:      Mapped[bool]          = mapped_column(Boolean, default=True)
    priority:       Mapped[int]            = mapped_column(Integer, default=100)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    category: Mapped["OffloadReasonCategory"] = relationship("OffloadReasonCategory", back_populates="rules")


# ═══════════════════════════════════════════════════════════════════════════════
# Backup Performance (backups normais de VM) — migration 0008
# Rotina = job_name + vm_name.  Execução = task_session_id (por VM).
# ═══════════════════════════════════════════════════════════════════════════════

class BackupImport(Base):
    """Rastreio de cada carga NDJSON de performance de backup (igual OffloadImport)."""
    __tablename__ = "backup_imports"
    __table_args__ = (
        Index("idx_bkpimp_client_time", "client_id", "imported_at"),
        Index("idx_bkpimp_file_hash",   "file_hash"),
        Index("idx_bkpimp_status",      "status"),
    )

    id:                 Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:          Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    filename:           Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename:  Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_hash:          Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    file_size_bytes:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status:             Mapped[str] = mapped_column(String(20), default="processing")
    status_message:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_lines:        Mapped[int] = mapped_column(Integer, default=0)
    inserted_sessions:  Mapped[int] = mapped_column(Integer, default=0)
    duplicate_sessions: Mapped[int] = mapped_column(Integer, default=0)
    ignored_lines:      Mapped[int] = mapped_column(Integer, default=0)
    failed_lines:       Mapped[int] = mapped_column(Integer, default=0)
    success_count:      Mapped[int] = mapped_column(Integer, default=0)
    warning_count:      Mapped[int] = mapped_column(Integer, default=0)
    failed_count:       Mapped[int] = mapped_column(Integer, default=0)
    min_started_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_started_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    uploaded_by:        Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    imported_at:        Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at:         Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:         Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class BackupRoutine(Base):
    """Rotina de backup (dimensão) — única por (client_id, job_name, vm_name)."""
    __tablename__ = "backup_routines"
    __table_args__ = (
        UniqueConstraint("client_id", "job_name", "vm_name", name="uq_bkprout_client_job_vm"),
        Index("idx_bkprout_client",     "client_id"),
        Index("idx_bkprout_client_job", "client_id", "job_name"),
        Index("idx_bkprout_vm",         "vm_name"),
    )

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:     Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    job_name:      Mapped[str] = mapped_column(String(500), nullable=False)
    vm_name:       Mapped[str] = mapped_column(String(500), nullable=False)
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_seen_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class BackupVmSession(Base):
    """
    Execução de backup de UMA VM (fato). Uma linha por task session.

    Dedup idempotente via dois índices únicos parciais:
      • UNIQUE(client_id, task_session_id) WHERE task_session_id IS NOT NULL
      • UNIQUE(client_id, content_hash)     WHERE task_session_id IS NULL
    """
    __tablename__ = "backup_vm_sessions"
    __table_args__ = (
        Index("uq_bkpsess_client_task", "client_id", "task_session_id",
              unique=True, postgresql_where=text("task_session_id IS NOT NULL")),
        Index("uq_bkpsess_client_hash", "client_id", "content_hash",
              unique=True, postgresql_where=text("task_session_id IS NULL")),
        Index("idx_bkpsess_rout_time",   "client_id", "backup_routine_id", "started_at"),
        Index("idx_bkpsess_result_time", "client_id", "result",            "started_at"),
        Index("idx_bkpsess_client_time", "client_id", "started_at"),
        Index("idx_bkpsess_import",      "backup_import_id"),
    )

    id:                Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:         Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    backup_routine_id: Mapped[int] = mapped_column(Integer, ForeignKey("backup_routines.id"), nullable=False)
    backup_import_id:  Mapped[int] = mapped_column(Integer, ForeignKey("backup_imports.id"), nullable=False)

    # ── Identificação da execução ─────────────────────────────────────────────
    job_session_id:    Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    task_session_id:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # dedup principal
    source_job_id:     Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    job_name_snapshot: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    vm_name:           Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ── Dados da execução ─────────────────────────────────────────────────────
    result:           Mapped[str]           = mapped_column(String(20), nullable=False)
    started_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at:         Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)

    # ── Performance ───────────────────────────────────────────────────────────
    processed_gb:     Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    read_gb:          Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    transferred_gb:   Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_speed_mbps:   Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    backup_type:      Mapped[Optional[str]]   = mapped_column(String(20), nullable=True)  # Full|Incremental
    reason_raw:       Mapped[Optional[str]]   = mapped_column(Text, nullable=True)

    # ── Dedup + rastreabilidade ───────────────────────────────────────────────
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    source:       Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at:   Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:   Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# Auditoria de Configuração de Rotinas (0009)
# ══════════════════════════════════════════════════════════════════════════════

class JobConfigImport(Base):
    """Rastreamento de cada carga de NDJSON de auditoria de configuração."""
    __tablename__ = "job_config_imports"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:         Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    filename:          Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_hash:         Mapped[Optional[str]] = mapped_column(String(64),  nullable=True)
    file_size_bytes:   Mapped[Optional[int]] = mapped_column(BigInteger,  nullable=True)
    status:            Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    status_message:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_lines:       Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_jobs:     Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_jobs:      Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_jobs:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ignored_lines:     Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_lines:      Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_objects:     Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_objects:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    script_version:    Mapped[Optional[str]] = mapped_column(String(20),  nullable=True)
    vbr_server:        Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    min_collected_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_collected_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    uploaded_by:       Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    imported_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at:        Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:        Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class JobConfigSnapshot(Base):
    """Snapshot da configuração de uma rotina em um momento de coleta."""
    __tablename__ = "job_config_snapshots"
    __table_args__ = (
        Index("uq_jcsnap_client_job_time", "client_id", "job_id", "collected_at",
              unique=True, postgresql_where=text("job_id IS NOT NULL")),
        Index("uq_jcsnap_client_hash", "client_id", "content_hash",
              unique=True, postgresql_where=text("job_id IS NULL")),
        Index("idx_jcsnap_client_name",  "client_id", "job_name"),
        Index("idx_jcsnap_client_time",  "client_id", "collected_at"),
        Index("idx_jcsnap_client_type",  "client_id", "job_type"),
        Index("idx_jcsnap_import",       "job_config_import_id"),
    )

    id:                   Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:            Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    job_config_import_id: Mapped[int] = mapped_column(Integer, ForeignKey("job_config_imports.id"), nullable=False)

    job_id:              Mapped[Optional[str]] = mapped_column(String(64),  nullable=True)
    job_name:            Mapped[str]           = mapped_column(String(500), nullable=False)
    job_name_normalized: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    job_type:            Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)
    platform:            Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)
    backup_type:         Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)
    job_description:     Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    is_enabled:          Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)
    is_schedule_enabled: Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)
    next_run:            Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    collected_at:   Mapped[Optional[datetime]] = mapped_column(DateTime,    nullable=True)
    vbr_server:     Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    computer_name:  Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    script_version: Mapped[Optional[str]]      = mapped_column(String(20),  nullable=True)

    sched_daily_enabled:        Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sched_daily_time:           Mapped[Optional[str]]  = mapped_column(String(20),  nullable=True)
    sched_daily_kind:           Mapped[Optional[str]]  = mapped_column(String(50),  nullable=True)
    sched_days_of_week:         Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    sched_periodically_enabled: Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sched_periodically_every:   Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    sched_periodically_unit:    Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    sched_retry_enabled:        Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sched_retry_count:          Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    sched_chain_job_name:       Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)

    repo_name:              Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)
    repo_id:                Mapped[Optional[str]]  = mapped_column(String(64),  nullable=True)
    repo_type:              Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    repo_is_sobr:           Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    repo_sobr_name:         Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)
    repo_immutability:      Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    repo_is_linux_hardened: Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    repo_per_vm_files:      Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    repo_extent_count:        Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    repo_extent_names:        Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)  # JSON
    repo_capacity_name:       Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)
    repo_capacity_immutable:  Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    repo_capacity_immut_days: Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)

    proxy_mode:            Mapped[Optional[str]]  = mapped_column(String(50),  nullable=True)
    proxy_automatic:       Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    proxy_selected_names:  Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)
    proxy_transport_modes: Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)

    gip_mode:      Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    gip_automatic: Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)

    aap_enabled:         Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)
    aap_mode:            Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    aap_require_success: Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)
    vmtools_quiesce:     Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)

    sql_detected:            Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sql_processing_enabled:  Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sql_tx_mode:             Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    sql_log_backup_enabled:  Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sql_log_freq_min:        Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    sql_log_retention_days:  Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    sql_per_object_detected: Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)

    ret_type:           Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    ret_storage_type:   Mapped[Optional[str]]  = mapped_column(String(30), nullable=True)  # Days|Cycles
    ret_restore_points: Mapped[Optional[int]]  = mapped_column(Integer,    nullable=True)
    ret_cycles:         Mapped[Optional[int]]  = mapped_column(Integer,    nullable=True)
    ret_days:           Mapped[Optional[int]]  = mapped_column(Integer,    nullable=True)
    ret_gfs_enabled:    Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)
    ret_gfs_weekly:     Mapped[Optional[int]]  = mapped_column(Integer,    nullable=True)
    ret_gfs_monthly:    Mapped[Optional[int]]  = mapped_column(Integer,    nullable=True)
    ret_gfs_yearly:     Mapped[Optional[int]]  = mapped_column(Integer,    nullable=True)

    stg_compression:   Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    stg_block_size:    Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    stg_dedup_enabled: Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)
    stg_encryption:    Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)
    stg_cbt_enabled:   Mapped[Optional[bool]] = mapped_column(Boolean,    nullable=True)

    # Quando roda cada tipo de full. No v12 isso vem de opt.BackupTargetOptions
    # (a Veeam grafa "Syntethic"); o liga/desliga do active full vem de
    # BackupStorageOptions.EnableFullBackup. Ver migração 0013.
    full_algorithm:         Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    synth_full_enabled:     Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    synth_full_kind:        Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    synth_full_days:        Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    synth_full_monthly:     Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    transform_to_rollbacks: Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    active_full_enabled:    Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    active_full_kind:       Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    active_full_days:       Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    active_full_monthly:    Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    compact_full_enabled:   Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    compact_full_kind:      Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    compact_full_days:      Mapped[Optional[str]]  = mapped_column(String(200), nullable=True)
    compact_full_monthly:   Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)

    content_hash: Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)
    created_at:   Mapped[datetime]           = mapped_column(DateTime,   default=datetime.utcnow)
    updated_at:   Mapped[Optional[datetime]] = mapped_column(DateTime,   nullable=True)


class JobConfigObject(Base):
    """Objeto/VM dentro de um snapshot de configuração (config efetiva por VM)."""
    __tablename__ = "job_config_objects"
    __table_args__ = (
        Index("uq_jcobj_snap_objid", "job_config_snapshot_id", "object_id",
              unique=True, postgresql_where=text("object_id IS NOT NULL")),
        Index("uq_jcobj_snap_objname", "job_config_snapshot_id", "object_name",
              unique=True, postgresql_where=text("object_id IS NULL")),
        Index("idx_jcobj_client",      "client_id"),
        Index("idx_jcobj_snap",        "job_config_snapshot_id"),
        Index("idx_jcobj_sql_enabled", "client_id", "sql_log_backup_enabled"),
    )

    id:                     Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:              Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    job_config_snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("job_config_snapshots.id"), nullable=False)

    object_id:   Mapped[Optional[str]] = mapped_column(String(64),  nullable=True)
    object_name: Mapped[str]           = mapped_column(String(500), nullable=False)
    object_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    object_path: Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    approx_size: Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)

    guest_processing_enabled:  Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    application_aware_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    sql_mode:               Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    sql_log_backup_enabled: Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sql_log_freq_min:       Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    sql_log_retain_days:    Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    sql_use_db_retention:   Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    sql_truncate_enabled:   Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ═══════════════════════ Uso de disco / capacidade de repositorio ══════════════

class DiskUsageImport(Base):
    """Rastreio de uma carga de uso de disco (collect_disk_backups +
    collect_repo_capacity, importados juntos por ambiente/cliente)."""
    __tablename__ = "disk_usage_imports"
    __table_args__ = (
        Index("idx_diskimp_client_time", "client_id", "imported_at"),
        Index("idx_diskimp_status",      "status"),
    )

    id:               Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:        Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    disk_filename:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    capacity_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status:           Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    status_message:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_lines:      Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_points:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_lines:     Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extents_count:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    logical_bytes:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    real_used_bytes:  Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    calib_factor:     Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_point_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_point_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    uploaded_by:      Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    imported_at:      Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DiskBackupPoint(Base):
    """Ponto de restauracao fisico em disco (dedup por rp_id dentro da carga)."""
    __tablename__ = "disk_backup_points"
    __table_args__ = (
        Index("uq_diskpt_import_rp", "disk_usage_import_id", "rp_id",
              unique=True, postgresql_where=text("rp_id IS NOT NULL")),
        Index("idx_diskpt_client_import", "client_id", "disk_usage_import_id"),
        Index("idx_diskpt_import_job",    "disk_usage_import_id", "job_name"),
        Index("idx_diskpt_import_vm",     "disk_usage_import_id", "vm_name"),
        Index("idx_diskpt_import_immut",  "disk_usage_import_id", "immutable_until"),
    )

    id:                   Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:            Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    disk_usage_import_id: Mapped[int] = mapped_column(Integer, ForeignKey("disk_usage_imports.id"), nullable=False)

    rp_id:            Mapped[Optional[str]]  = mapped_column(String(64),  nullable=True)
    job_name:         Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)
    backup_name:      Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)
    vm_name:          Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)
    repository:       Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    extent_name:      Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    tier:             Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    restore_point_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_full:          Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    backup_type:      Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    gfs_period:       Mapped[Optional[str]]  = mapped_column(String(30),  nullable=True)
    size_bytes:       Mapped[Optional[int]]  = mapped_column(BigInteger,  nullable=True)
    data_size_bytes:  Mapped[Optional[int]]  = mapped_column(BigInteger,  nullable=True)
    dedup_ratio:      Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    compress_ratio:   Mapped[Optional[int]]  = mapped_column(Integer,     nullable=True)
    is_immutable:     Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    immutable_until:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    in_capacity_tier: Mapped[Optional[bool]] = mapped_column(Boolean,     nullable=True)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RepoCapacitySample(Base):
    """Espaco total/livre/usado de um extent/repo no momento da coleta."""
    __tablename__ = "repo_capacity_samples"
    __table_args__ = (
        Index("idx_repocap_client_import", "client_id", "disk_usage_import_id"),
        Index("idx_repocap_import_tier",   "disk_usage_import_id", "tier"),
    )

    id:                   Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:            Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    disk_usage_import_id: Mapped[int] = mapped_column(Integer, ForeignKey("disk_usage_imports.id"), nullable=False)

    tier:        Mapped[Optional[str]] = mapped_column(String(30),  nullable=True)
    sobr_name:   Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    name:        Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    repo_type:   Mapped[Optional[str]] = mapped_column(String(80),  nullable=True)
    total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger,  nullable=True)
    free_bytes:  Mapped[Optional[int]] = mapped_column(BigInteger,  nullable=True)
    used_bytes:  Mapped[Optional[int]] = mapped_column(BigInteger,  nullable=True)
    path:        Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobEnvironment(Base):
    """Classificacao de ambiente de uma rotina: 'prod' (default) ou 'hml'."""
    __tablename__ = "job_environments"
    __table_args__ = (
        Index("uq_jobenv_client_job", "client_id", "job_name", unique=True),
    )
    id:          Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:   Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    job_name:    Mapped[str] = mapped_column(String(500), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False, default="prod")
    updated_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

