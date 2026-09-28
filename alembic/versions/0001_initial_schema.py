"""Schema inicial.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_DDL = r"""
CREATE TABLE public.backup_imports (
    id integer NOT NULL,
    client_id integer NOT NULL,
    filename character varying(255) NOT NULL,
    original_filename character varying(255),
    file_hash character varying(64),
    file_size_bytes bigint,
    status character varying(20) DEFAULT 'processing'::character varying NOT NULL,
    status_message text,
    total_lines integer DEFAULT 0 NOT NULL,
    inserted_sessions integer DEFAULT 0 NOT NULL,
    duplicate_sessions integer DEFAULT 0 NOT NULL,
    ignored_lines integer DEFAULT 0 NOT NULL,
    failed_lines integer DEFAULT 0 NOT NULL,
    success_count integer DEFAULT 0 NOT NULL,
    warning_count integer DEFAULT 0 NOT NULL,
    failed_count integer DEFAULT 0 NOT NULL,
    min_started_at timestamp without time zone,
    max_started_at timestamp without time zone,
    uploaded_by character varying(100),
    imported_at timestamp without time zone DEFAULT now(),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.backup_imports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.backup_imports_id_seq OWNED BY public.backup_imports.id;
CREATE TABLE public.backup_routines (
    id integer NOT NULL,
    client_id integer NOT NULL,
    job_name character varying(500) NOT NULL,
    vm_name character varying(500) NOT NULL,
    first_seen_at timestamp without time zone,
    last_seen_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.backup_routines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.backup_routines_id_seq OWNED BY public.backup_routines.id;
CREATE TABLE public.backup_vm_sessions (
    id integer NOT NULL,
    client_id integer NOT NULL,
    backup_routine_id integer NOT NULL,
    backup_import_id integer NOT NULL,
    job_session_id character varying(64),
    task_session_id character varying(64),
    source_job_id character varying(64),
    job_name_snapshot character varying(500),
    vm_name character varying(500),
    result character varying(20) NOT NULL,
    started_at timestamp without time zone,
    ended_at timestamp without time zone,
    duration_seconds integer,
    processed_gb double precision,
    read_gb double precision,
    transferred_gb double precision,
    avg_speed_mbps double precision,
    backup_type character varying(20),
    reason_raw text,
    content_hash character varying(64),
    collected_at timestamp without time zone,
    imported_at timestamp without time zone DEFAULT now(),
    source character varying(255),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.backup_vm_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.backup_vm_sessions_id_seq OWNED BY public.backup_vm_sessions.id;
CREATE TABLE public.branding_profiles (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    is_default boolean NOT NULL,
    is_base boolean NOT NULL,
    client_id integer,
    system_name character varying(200),
    short_name character varying(100),
    company_name character varying(200),
    header_text character varying(500),
    footer_text character varying(500),
    website_url character varying(300),
    contact_email character varying(200),
    primary_color character varying(7),
    secondary_color character varying(7),
    accent_color character varying(7),
    success_color character varying(7),
    warning_color character varying(7),
    danger_color character varying(7),
    background_color character varying(7),
    card_bg_color character varying(7),
    table_header_color character varying(7),
    text_primary_color character varying(7),
    text_secondary_color character varying(7),
    logo_main_path character varying(500),
    logo_login_path character varying(500),
    logo_report_path character varying(500),
    favicon_path character varying(500),
    pptx_cover_path character varying(500),
    pptx_final_slide_path character varying(500),
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone,
    created_by character varying(100),
    updated_by character varying(100)
);
CREATE SEQUENCE public.branding_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.branding_profiles_id_seq OWNED BY public.branding_profiles.id;
CREATE TABLE public.clients (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    is_active boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE public.clients_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.clients_id_seq OWNED BY public.clients.id;
CREATE TABLE public.disk_backup_points (
    id integer NOT NULL,
    client_id integer NOT NULL,
    disk_usage_import_id integer NOT NULL,
    rp_id character varying(64),
    job_name character varying(500),
    backup_name character varying(500),
    vm_name character varying(500),
    repository character varying(255),
    extent_name character varying(255),
    tier character varying(30),
    restore_point_at timestamp without time zone,
    is_full boolean,
    backup_type character varying(30),
    gfs_period character varying(30),
    size_bytes bigint,
    data_size_bytes bigint,
    dedup_ratio integer,
    compress_ratio integer,
    is_immutable boolean,
    immutable_until timestamp without time zone,
    in_capacity_tier boolean,
    created_at timestamp without time zone DEFAULT now()
);
CREATE SEQUENCE public.disk_backup_points_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.disk_backup_points_id_seq OWNED BY public.disk_backup_points.id;
CREATE TABLE public.disk_usage_imports (
    id integer NOT NULL,
    client_id integer NOT NULL,
    disk_filename character varying(255),
    capacity_filename character varying(255),
    status character varying(20) DEFAULT 'processing'::character varying NOT NULL,
    status_message text,
    total_lines integer DEFAULT 0 NOT NULL,
    inserted_points integer DEFAULT 0 NOT NULL,
    duplicate_points integer DEFAULT 0 NOT NULL,
    failed_lines integer DEFAULT 0 NOT NULL,
    extents_count integer DEFAULT 0 NOT NULL,
    logical_bytes bigint,
    real_used_bytes bigint,
    calib_factor double precision,
    min_point_at timestamp without time zone,
    max_point_at timestamp without time zone,
    uploaded_by character varying(100),
    imported_at timestamp without time zone DEFAULT now(),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.disk_usage_imports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.disk_usage_imports_id_seq OWNED BY public.disk_usage_imports.id;
CREATE TABLE public.imported_events (
    id integer NOT NULL,
    upload_session_id integer NOT NULL,
    client_id integer NOT NULL,
    content_hash character varying(64),
    raw_event_xml text,
    raw_event_json text,
    original_message text,
    provider_name character varying(255),
    channel character varying(255),
    record_id bigint,
    source_filename character varying(500),
    imported_at timestamp without time zone NOT NULL,
    event_id integer,
    time_created timestamp without time zone,
    vbr_hostname character varying(255),
    event_category character varying(20) NOT NULL,
    job_name character varying(500),
    job_name_normalized character varying(500),
    job_type character varying(20) NOT NULL,
    job_result integer,
    will_be_retried boolean,
    operator character varying(255),
    audit_event_type character varying(50),
    audit_event_label character varying(100),
    audit_details text,
    parser_version character varying(20),
    parsed_at timestamp without time zone NOT NULL,
    offender_category_id integer,
    offender_rule_id integer,
    offender_match_pattern character varying(500),
    offender_classified_at timestamp without time zone
);
CREATE SEQUENCE public.imported_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.imported_events_id_seq OWNED BY public.imported_events.id;
CREATE TABLE public.job_actions (
    id integer NOT NULL,
    client_id integer NOT NULL,
    job_name_normalized character varying(500) NOT NULL,
    situation character varying(50) NOT NULL,
    action_ongoing text NOT NULL,
    technical_observation text NOT NULL,
    responsible character varying(255) NOT NULL,
    ticket character varying(255) NOT NULL,
    updated_at timestamp without time zone,
    updated_by character varying(100)
);
CREATE SEQUENCE public.job_actions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.job_actions_id_seq OWNED BY public.job_actions.id;
CREATE TABLE public.job_config_imports (
    id integer NOT NULL,
    client_id integer NOT NULL,
    filename character varying(255) NOT NULL,
    original_filename character varying(255),
    file_hash character varying(64),
    file_size_bytes bigint,
    status character varying(20) DEFAULT 'processing'::character varying NOT NULL,
    status_message text,
    total_lines integer DEFAULT 0 NOT NULL,
    inserted_jobs integer DEFAULT 0 NOT NULL,
    updated_jobs integer DEFAULT 0 NOT NULL,
    duplicate_jobs integer DEFAULT 0 NOT NULL,
    ignored_lines integer DEFAULT 0 NOT NULL,
    failed_lines integer DEFAULT 0 NOT NULL,
    total_objects integer DEFAULT 0 NOT NULL,
    inserted_objects integer DEFAULT 0 NOT NULL,
    script_version character varying(20),
    vbr_server character varying(255),
    min_collected_at timestamp without time zone,
    max_collected_at timestamp without time zone,
    uploaded_by character varying(100),
    imported_at timestamp without time zone DEFAULT now(),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.job_config_imports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.job_config_imports_id_seq OWNED BY public.job_config_imports.id;
CREATE TABLE public.job_config_objects (
    id integer NOT NULL,
    client_id integer NOT NULL,
    job_config_snapshot_id integer NOT NULL,
    object_id character varying(64),
    object_name character varying(500) NOT NULL,
    object_type character varying(100),
    object_path text,
    approx_size character varying(50),
    guest_processing_enabled boolean,
    application_aware_enabled boolean,
    sql_mode character varying(100),
    sql_log_backup_enabled boolean,
    sql_log_freq_min integer,
    sql_log_retain_days integer,
    sql_use_db_retention boolean,
    sql_truncate_enabled boolean,
    created_at timestamp without time zone DEFAULT now()
);
CREATE SEQUENCE public.job_config_objects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.job_config_objects_id_seq OWNED BY public.job_config_objects.id;
CREATE TABLE public.job_config_snapshots (
    id integer NOT NULL,
    client_id integer NOT NULL,
    job_config_import_id integer NOT NULL,
    job_id character varying(64),
    job_name character varying(500) NOT NULL,
    job_name_normalized character varying(500),
    job_type character varying(50),
    platform character varying(50),
    backup_type character varying(50),
    job_description text,
    is_enabled boolean,
    is_schedule_enabled boolean,
    next_run character varying(100),
    collected_at timestamp without time zone,
    vbr_server character varying(255),
    computer_name character varying(255),
    script_version character varying(20),
    sched_daily_enabled boolean,
    sched_daily_time character varying(20),
    sched_daily_kind character varying(50),
    sched_days_of_week character varying(100),
    sched_periodically_enabled boolean,
    sched_periodically_every integer,
    sched_periodically_unit character varying(30),
    sched_retry_enabled boolean,
    sched_retry_count integer,
    sched_chain_job_name character varying(500),
    repo_name character varying(500),
    repo_id character varying(64),
    repo_type character varying(100),
    repo_is_sobr boolean,
    repo_sobr_name character varying(500),
    repo_immutability boolean,
    repo_is_linux_hardened boolean,
    repo_per_vm_files boolean,
    proxy_mode character varying(50),
    proxy_automatic boolean,
    proxy_selected_names text,
    proxy_transport_modes text,
    gip_mode character varying(50),
    gip_automatic boolean,
    aap_enabled boolean,
    aap_mode character varying(50),
    aap_require_success boolean,
    vmtools_quiesce boolean,
    sql_detected boolean,
    sql_processing_enabled boolean,
    sql_tx_mode character varying(100),
    sql_log_backup_enabled boolean,
    sql_log_freq_min integer,
    sql_log_retention_days integer,
    sql_per_object_detected boolean,
    ret_type character varying(50),
    ret_restore_points integer,
    ret_days integer,
    ret_gfs_enabled boolean,
    ret_gfs_weekly integer,
    ret_gfs_monthly integer,
    ret_gfs_yearly integer,
    stg_compression character varying(50),
    stg_block_size character varying(50),
    stg_dedup_enabled boolean,
    stg_encryption boolean,
    stg_cbt_enabled boolean,
    content_hash character varying(64),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone,
    ret_storage_type character varying(30),
    ret_cycles integer,
    repo_extent_count integer,
    repo_extent_names text,
    repo_capacity_name character varying(500),
    repo_capacity_immutable boolean,
    repo_capacity_immut_days integer,
    full_algorithm character varying(30),
    synth_full_enabled boolean,
    synth_full_kind character varying(30),
    synth_full_days character varying(200),
    synth_full_monthly character varying(100),
    transform_to_rollbacks boolean,
    active_full_enabled boolean,
    active_full_kind character varying(30),
    active_full_days character varying(200),
    active_full_monthly character varying(100),
    compact_full_enabled boolean,
    compact_full_kind character varying(30),
    compact_full_days character varying(200),
    compact_full_monthly character varying(100)
);
CREATE SEQUENCE public.job_config_snapshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.job_config_snapshots_id_seq OWNED BY public.job_config_snapshots.id;
CREATE TABLE public.job_environments (
    id integer NOT NULL,
    client_id integer NOT NULL,
    job_name character varying(500) NOT NULL,
    environment character varying(20) DEFAULT 'prod'::character varying NOT NULL,
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.job_environments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.job_environments_id_seq OWNED BY public.job_environments.id;
CREATE TABLE public.job_summaries (
    id integer NOT NULL,
    report_id integer NOT NULL,
    job_name character varying(500) NOT NULL,
    job_name_normalized character varying(500) NOT NULL,
    job_type character varying(20) NOT NULL,
    total_executions integer NOT NULL,
    failed_count integer NOT NULL,
    warning_count integer NOT NULL,
    success_count integer NOT NULL,
    last3_summary character varying(50) NOT NULL,
    situation character varying(50) NOT NULL,
    last_error_message text NOT NULL,
    action_ongoing text NOT NULL,
    technical_observation text NOT NULL,
    responsible character varying(255) NOT NULL,
    ticket character varying(255) NOT NULL
);
CREATE SEQUENCE public.job_summaries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.job_summaries_id_seq OWNED BY public.job_summaries.id;
CREATE TABLE public.offender_categories (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    severity character varying(20) DEFAULT 'medium'::character varying NOT NULL,
    color character varying(7),
    is_active boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    notes text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.offender_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.offender_categories_id_seq OWNED BY public.offender_categories.id;
CREATE TABLE public.offender_rules (
    id integer NOT NULL,
    category_id integer NOT NULL,
    pattern character varying(500) NOT NULL,
    match_type character varying(20) DEFAULT 'contains'::character varying NOT NULL,
    case_sensitive boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.offender_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.offender_rules_id_seq OWNED BY public.offender_rules.id;
CREATE TABLE public.offload_imports (
    id integer NOT NULL,
    client_id integer NOT NULL,
    filename character varying(255) NOT NULL,
    original_filename character varying(255),
    file_hash character varying(64),
    file_size_bytes bigint,
    status character varying(20) DEFAULT 'processing'::character varying NOT NULL,
    status_message text,
    total_lines integer DEFAULT 0 NOT NULL,
    inserted_sessions integer DEFAULT 0 NOT NULL,
    duplicate_sessions integer DEFAULT 0 NOT NULL,
    ignored_lines integer DEFAULT 0 NOT NULL,
    failed_lines integer DEFAULT 0 NOT NULL,
    success_count integer DEFAULT 0 NOT NULL,
    warning_count integer DEFAULT 0 NOT NULL,
    failed_count integer DEFAULT 0 NOT NULL,
    min_started_at timestamp without time zone,
    max_started_at timestamp without time zone,
    uploaded_by character varying(100),
    imported_at timestamp without time zone DEFAULT now(),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.offload_imports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.offload_imports_id_seq OWNED BY public.offload_imports.id;
CREATE TABLE public.offload_jobs (
    id integer NOT NULL,
    client_id integer NOT NULL,
    job_name character varying(500) NOT NULL,
    job_name_normalized character varying(500) NOT NULL,
    first_seen_at timestamp without time zone,
    last_seen_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.offload_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.offload_jobs_id_seq OWNED BY public.offload_jobs.id;
CREATE TABLE public.offload_reason_categories (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    severity character varying(20) DEFAULT 'medium'::character varying NOT NULL,
    color character varying(7),
    is_active boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.offload_reason_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.offload_reason_categories_id_seq OWNED BY public.offload_reason_categories.id;
CREATE TABLE public.offload_reason_rules (
    id integer NOT NULL,
    category_id integer NOT NULL,
    pattern character varying(500) NOT NULL,
    match_type character varying(20) DEFAULT 'contains'::character varying NOT NULL,
    case_sensitive boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.offload_reason_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.offload_reason_rules_id_seq OWNED BY public.offload_reason_rules.id;
CREATE TABLE public.offload_sessions (
    id integer NOT NULL,
    client_id integer NOT NULL,
    offload_job_id integer NOT NULL,
    offload_import_id integer NOT NULL,
    session_id character varying(64),
    source_job_id character varying(64),
    job_name_snapshot character varying(500),
    result character varying(20) NOT NULL,
    state character varying(50),
    started_at timestamp without time zone,
    ended_at timestamp without time zone,
    duration_seconds integer,
    reason_raw text,
    reason_normalized text,
    reason_category_id integer,
    reason_rule_id integer,
    matched_pattern character varying(500),
    target_ip character varying(45),
    target_port integer,
    content_hash character varying(64),
    collected_at timestamp without time zone,
    imported_at timestamp without time zone DEFAULT now(),
    source character varying(255),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.offload_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.offload_sessions_id_seq OWNED BY public.offload_sessions.id;
CREATE TABLE public.password_history (
    id integer NOT NULL,
    user_id integer NOT NULL,
    password_hash character varying(255) NOT NULL,
    changed_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE public.password_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.password_history_id_seq OWNED BY public.password_history.id;
CREATE TABLE public.password_policy (
    id integer NOT NULL,
    min_length integer NOT NULL,
    require_uppercase boolean NOT NULL,
    require_lowercase boolean NOT NULL,
    require_digits boolean NOT NULL,
    require_special boolean NOT NULL,
    history_count integer NOT NULL,
    expiry_days integer NOT NULL,
    max_attempts integer NOT NULL,
    lockout_minutes integer NOT NULL
);
CREATE SEQUENCE public.password_policy_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.password_policy_id_seq OWNED BY public.password_policy.id;
CREATE TABLE public.repo_capacity_samples (
    id integer NOT NULL,
    client_id integer NOT NULL,
    disk_usage_import_id integer NOT NULL,
    tier character varying(30),
    sobr_name character varying(255),
    name character varying(255),
    repo_type character varying(80),
    total_bytes bigint,
    free_bytes bigint,
    used_bytes bigint,
    path character varying(500),
    created_at timestamp without time zone DEFAULT now()
);
CREATE SEQUENCE public.repo_capacity_samples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.repo_capacity_samples_id_seq OWNED BY public.repo_capacity_samples.id;
CREATE TABLE public.report_sessions (
    id integer NOT NULL,
    client_id integer NOT NULL,
    client_name character varying(255) NOT NULL,
    date_start timestamp without time zone NOT NULL,
    date_end timestamp without time zone NOT NULL,
    created_by character varying(100),
    created_at timestamp without time zone NOT NULL,
    include_backup boolean NOT NULL,
    include_log_backup boolean NOT NULL,
    show_all_jobs boolean NOT NULL,
    is_snapshot boolean NOT NULL
);
CREATE SEQUENCE public.report_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.report_sessions_id_seq OWNED BY public.report_sessions.id;
CREATE TABLE public.rpo_policies (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    expected_minutes integer NOT NULL,
    severity_default character varying(20) DEFAULT 'medium'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.rpo_policies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.rpo_policies_id_seq OWNED BY public.rpo_policies.id;
CREATE TABLE public.security_log (
    id integer NOT NULL,
    event_type character varying(50) NOT NULL,
    username character varying(100),
    actor character varying(100),
    ip_address character varying(45),
    details text,
    created_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE public.security_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.security_log_id_seq OWNED BY public.security_log.id;
CREATE TABLE public.upload_sessions (
    id integer NOT NULL,
    client_id integer NOT NULL,
    original_filename character varying(255) NOT NULL,
    stored_filename character varying(255) NOT NULL,
    file_format character varying(10) NOT NULL,
    uploaded_by character varying(100),
    uploaded_at timestamp without time zone NOT NULL,
    status character varying(20) NOT NULL,
    status_message text NOT NULL,
    error_message text,
    period_start timestamp without time zone,
    period_end timestamp without time zone,
    total_events_read integer NOT NULL,
    new_events_inserted integer NOT NULL,
    duplicate_events_skipped integer NOT NULL,
    invalid_events_skipped integer NOT NULL,
    backup_events_count integer NOT NULL,
    audit_events_count integer NOT NULL
);
CREATE SEQUENCE public.upload_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.upload_sessions_id_seq OWNED BY public.upload_sessions.id;
CREATE TABLE public.user_clients (
    id integer NOT NULL,
    user_id integer NOT NULL,
    client_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    created_by character varying(100)
);
CREATE SEQUENCE public.user_clients_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.user_clients_id_seq OWNED BY public.user_clients.id;
CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(100) NOT NULL,
    email character varying(255),
    full_name character varying(255),
    password_hash character varying(255) NOT NULL,
    is_active boolean NOT NULL,
    is_admin boolean NOT NULL,
    must_change_password boolean NOT NULL,
    failed_attempts integer NOT NULL,
    locked_until timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    created_by character varying(100),
    last_login timestamp without time zone,
    last_password_change timestamp without time zone,
    password_expires_at timestamp without time zone
);
CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;
CREATE TABLE public.vm_rpo_assignments (
    id integer NOT NULL,
    client_id integer NOT NULL,
    vm_name character varying(500) NOT NULL,
    vm_name_normalized character varying(500),
    rpo_policy_id integer,
    expected_minutes integer NOT NULL,
    criticality character varying(20),
    notes text,
    is_active boolean DEFAULT true NOT NULL,
    created_by character varying(100),
    updated_by character varying(100),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone
);
CREATE SEQUENCE public.vm_rpo_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.vm_rpo_assignments_id_seq OWNED BY public.vm_rpo_assignments.id;
ALTER TABLE ONLY public.backup_imports ALTER COLUMN id SET DEFAULT nextval('public.backup_imports_id_seq'::regclass);
ALTER TABLE ONLY public.backup_routines ALTER COLUMN id SET DEFAULT nextval('public.backup_routines_id_seq'::regclass);
ALTER TABLE ONLY public.backup_vm_sessions ALTER COLUMN id SET DEFAULT nextval('public.backup_vm_sessions_id_seq'::regclass);
ALTER TABLE ONLY public.branding_profiles ALTER COLUMN id SET DEFAULT nextval('public.branding_profiles_id_seq'::regclass);
ALTER TABLE ONLY public.clients ALTER COLUMN id SET DEFAULT nextval('public.clients_id_seq'::regclass);
ALTER TABLE ONLY public.disk_backup_points ALTER COLUMN id SET DEFAULT nextval('public.disk_backup_points_id_seq'::regclass);
ALTER TABLE ONLY public.disk_usage_imports ALTER COLUMN id SET DEFAULT nextval('public.disk_usage_imports_id_seq'::regclass);
ALTER TABLE ONLY public.imported_events ALTER COLUMN id SET DEFAULT nextval('public.imported_events_id_seq'::regclass);
ALTER TABLE ONLY public.job_actions ALTER COLUMN id SET DEFAULT nextval('public.job_actions_id_seq'::regclass);
ALTER TABLE ONLY public.job_config_imports ALTER COLUMN id SET DEFAULT nextval('public.job_config_imports_id_seq'::regclass);
ALTER TABLE ONLY public.job_config_objects ALTER COLUMN id SET DEFAULT nextval('public.job_config_objects_id_seq'::regclass);
ALTER TABLE ONLY public.job_config_snapshots ALTER COLUMN id SET DEFAULT nextval('public.job_config_snapshots_id_seq'::regclass);
ALTER TABLE ONLY public.job_environments ALTER COLUMN id SET DEFAULT nextval('public.job_environments_id_seq'::regclass);
ALTER TABLE ONLY public.job_summaries ALTER COLUMN id SET DEFAULT nextval('public.job_summaries_id_seq'::regclass);
ALTER TABLE ONLY public.offender_categories ALTER COLUMN id SET DEFAULT nextval('public.offender_categories_id_seq'::regclass);
ALTER TABLE ONLY public.offender_rules ALTER COLUMN id SET DEFAULT nextval('public.offender_rules_id_seq'::regclass);
ALTER TABLE ONLY public.offload_imports ALTER COLUMN id SET DEFAULT nextval('public.offload_imports_id_seq'::regclass);
ALTER TABLE ONLY public.offload_jobs ALTER COLUMN id SET DEFAULT nextval('public.offload_jobs_id_seq'::regclass);
ALTER TABLE ONLY public.offload_reason_categories ALTER COLUMN id SET DEFAULT nextval('public.offload_reason_categories_id_seq'::regclass);
ALTER TABLE ONLY public.offload_reason_rules ALTER COLUMN id SET DEFAULT nextval('public.offload_reason_rules_id_seq'::regclass);
ALTER TABLE ONLY public.offload_sessions ALTER COLUMN id SET DEFAULT nextval('public.offload_sessions_id_seq'::regclass);
ALTER TABLE ONLY public.password_history ALTER COLUMN id SET DEFAULT nextval('public.password_history_id_seq'::regclass);
ALTER TABLE ONLY public.password_policy ALTER COLUMN id SET DEFAULT nextval('public.password_policy_id_seq'::regclass);
ALTER TABLE ONLY public.repo_capacity_samples ALTER COLUMN id SET DEFAULT nextval('public.repo_capacity_samples_id_seq'::regclass);
ALTER TABLE ONLY public.report_sessions ALTER COLUMN id SET DEFAULT nextval('public.report_sessions_id_seq'::regclass);
ALTER TABLE ONLY public.rpo_policies ALTER COLUMN id SET DEFAULT nextval('public.rpo_policies_id_seq'::regclass);
ALTER TABLE ONLY public.security_log ALTER COLUMN id SET DEFAULT nextval('public.security_log_id_seq'::regclass);
ALTER TABLE ONLY public.upload_sessions ALTER COLUMN id SET DEFAULT nextval('public.upload_sessions_id_seq'::regclass);
ALTER TABLE ONLY public.user_clients ALTER COLUMN id SET DEFAULT nextval('public.user_clients_id_seq'::regclass);
ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);
ALTER TABLE ONLY public.vm_rpo_assignments ALTER COLUMN id SET DEFAULT nextval('public.vm_rpo_assignments_id_seq'::regclass);
ALTER TABLE ONLY public.backup_imports
    ADD CONSTRAINT backup_imports_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.backup_routines
    ADD CONSTRAINT backup_routines_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.backup_vm_sessions
    ADD CONSTRAINT backup_vm_sessions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.branding_profiles
    ADD CONSTRAINT branding_profiles_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.disk_backup_points
    ADD CONSTRAINT disk_backup_points_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.disk_usage_imports
    ADD CONSTRAINT disk_usage_imports_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.imported_events
    ADD CONSTRAINT imported_events_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.job_actions
    ADD CONSTRAINT job_actions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.job_config_imports
    ADD CONSTRAINT job_config_imports_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.job_config_objects
    ADD CONSTRAINT job_config_objects_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.job_config_snapshots
    ADD CONSTRAINT job_config_snapshots_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.job_environments
    ADD CONSTRAINT job_environments_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.job_summaries
    ADD CONSTRAINT job_summaries_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.offender_categories
    ADD CONSTRAINT offender_categories_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.offender_rules
    ADD CONSTRAINT offender_rules_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.offload_imports
    ADD CONSTRAINT offload_imports_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.offload_jobs
    ADD CONSTRAINT offload_jobs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.offload_reason_categories
    ADD CONSTRAINT offload_reason_categories_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.offload_reason_rules
    ADD CONSTRAINT offload_reason_rules_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.offload_sessions
    ADD CONSTRAINT offload_sessions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.password_history
    ADD CONSTRAINT password_history_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.password_policy
    ADD CONSTRAINT password_policy_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.repo_capacity_samples
    ADD CONSTRAINT repo_capacity_samples_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.report_sessions
    ADD CONSTRAINT report_sessions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.rpo_policies
    ADD CONSTRAINT rpo_policies_name_key UNIQUE (name);
ALTER TABLE ONLY public.rpo_policies
    ADD CONSTRAINT rpo_policies_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.security_log
    ADD CONSTRAINT security_log_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.upload_sessions
    ADD CONSTRAINT upload_sessions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.backup_routines
    ADD CONSTRAINT uq_bkprout_client_job_vm UNIQUE (client_id, job_name, vm_name);
ALTER TABLE ONLY public.clients
    ADD CONSTRAINT uq_clients_name UNIQUE (name);
ALTER TABLE ONLY public.imported_events
    ADD CONSTRAINT uq_events_client_hash UNIQUE (client_id, content_hash);
ALTER TABLE ONLY public.job_actions
    ADD CONSTRAINT uq_job_actions_client_job UNIQUE (client_id, job_name_normalized);
ALTER TABLE ONLY public.offload_jobs
    ADD CONSTRAINT uq_offjob_client_norm UNIQUE (client_id, job_name_normalized);
ALTER TABLE ONLY public.user_clients
    ADD CONSTRAINT uq_user_client UNIQUE (user_id, client_id);
ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_username UNIQUE (username);
ALTER TABLE ONLY public.vm_rpo_assignments
    ADD CONSTRAINT uq_vmrpo_client_vm UNIQUE (client_id, vm_name);
ALTER TABLE ONLY public.user_clients
    ADD CONSTRAINT user_clients_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.vm_rpo_assignments
    ADD CONSTRAINT vm_rpo_assignments_pkey PRIMARY KEY (id);
CREATE INDEX idx_bkpimp_client_time ON public.backup_imports USING btree (client_id, imported_at);
CREATE INDEX idx_bkpimp_file_hash ON public.backup_imports USING btree (file_hash);
CREATE INDEX idx_bkpimp_status ON public.backup_imports USING btree (status);
CREATE INDEX idx_bkprout_client ON public.backup_routines USING btree (client_id);
CREATE INDEX idx_bkprout_client_job ON public.backup_routines USING btree (client_id, job_name);
CREATE INDEX idx_bkprout_vm ON public.backup_routines USING btree (vm_name);
CREATE INDEX idx_bkpsess_client_time ON public.backup_vm_sessions USING btree (client_id, started_at);
CREATE INDEX idx_bkpsess_import ON public.backup_vm_sessions USING btree (backup_import_id);
CREATE INDEX idx_bkpsess_result_time ON public.backup_vm_sessions USING btree (client_id, result, started_at);
CREATE INDEX idx_bkpsess_rout_time ON public.backup_vm_sessions USING btree (client_id, backup_routine_id, started_at);
CREATE INDEX idx_branding_client_id ON public.branding_profiles USING btree (client_id);
CREATE INDEX idx_branding_default ON public.branding_profiles USING btree (is_default);
CREATE INDEX idx_diskimp_client_time ON public.disk_usage_imports USING btree (client_id, imported_at);
CREATE INDEX idx_diskimp_status ON public.disk_usage_imports USING btree (status);
CREATE INDEX idx_diskpt_client_import ON public.disk_backup_points USING btree (client_id, disk_usage_import_id);
CREATE INDEX idx_diskpt_import_immut ON public.disk_backup_points USING btree (disk_usage_import_id, immutable_until);
CREATE INDEX idx_diskpt_import_job ON public.disk_backup_points USING btree (disk_usage_import_id, job_name);
CREATE INDEX idx_diskpt_import_vm ON public.disk_backup_points USING btree (disk_usage_import_id, vm_name);
CREATE INDEX idx_ev_client_audit_time ON public.imported_events USING btree (client_id, audit_event_type, time_created);
CREATE INDEX idx_ev_client_cat ON public.imported_events USING btree (client_id, event_category);
CREATE INDEX idx_ev_client_cat_time ON public.imported_events USING btree (client_id, event_category, time_created);
CREATE INDEX idx_ev_client_eid_time ON public.imported_events USING btree (client_id, event_id, time_created);
CREATE INDEX idx_ev_client_job ON public.imported_events USING btree (client_id, job_name_normalized);
CREATE INDEX idx_ev_client_job_time ON public.imported_events USING btree (client_id, job_name_normalized, time_created);
CREATE INDEX idx_ev_client_result ON public.imported_events USING btree (client_id, job_result);
CREATE INDEX idx_ev_client_result_time ON public.imported_events USING btree (client_id, job_result, time_created);
CREATE INDEX idx_ev_client_time ON public.imported_events USING btree (client_id, time_created);
CREATE INDEX idx_ev_event_id ON public.imported_events USING btree (event_id);
CREATE INDEX idx_ev_offender_cat ON public.imported_events USING btree (offender_category_id);
CREATE INDEX idx_ev_upload_session ON public.imported_events USING btree (upload_session_id);
CREATE INDEX idx_ev_vbr_hostname ON public.imported_events USING btree (vbr_hostname);
CREATE INDEX idx_jcimp_client_time ON public.job_config_imports USING btree (client_id, imported_at);
CREATE INDEX idx_jcimp_file_hash ON public.job_config_imports USING btree (file_hash);
CREATE INDEX idx_jcimp_status ON public.job_config_imports USING btree (status);
CREATE INDEX idx_jcobj_client ON public.job_config_objects USING btree (client_id);
CREATE INDEX idx_jcobj_snap ON public.job_config_objects USING btree (job_config_snapshot_id);
CREATE INDEX idx_jcobj_sql_enabled ON public.job_config_objects USING btree (client_id, sql_log_backup_enabled);
CREATE INDEX idx_jcsnap_client_name ON public.job_config_snapshots USING btree (client_id, job_name);
CREATE INDEX idx_jcsnap_client_time ON public.job_config_snapshots USING btree (client_id, collected_at);
CREATE INDEX idx_jcsnap_client_type ON public.job_config_snapshots USING btree (client_id, job_type);
CREATE INDEX idx_jcsnap_import ON public.job_config_snapshots USING btree (job_config_import_id);
CREATE INDEX idx_job_actions_client ON public.job_actions USING btree (client_id);
CREATE INDEX idx_job_summary_report ON public.job_summaries USING btree (report_id);
CREATE INDEX idx_offcat_active_prio ON public.offender_categories USING btree (is_active, priority);
CREATE INDEX idx_offimp_client_time ON public.offload_imports USING btree (client_id, imported_at);
CREATE INDEX idx_offimp_file_hash ON public.offload_imports USING btree (file_hash);
CREATE INDEX idx_offimp_status ON public.offload_imports USING btree (status);
CREATE INDEX idx_offjob_client ON public.offload_jobs USING btree (client_id);
CREATE INDEX idx_offjob_client_norm ON public.offload_jobs USING btree (client_id, job_name_normalized);
CREATE INDEX idx_offjob_norm ON public.offload_jobs USING btree (job_name_normalized);
CREATE INDEX idx_offreasoncat_active_prio ON public.offload_reason_categories USING btree (is_active, priority);
CREATE INDEX idx_offreasonrule_cat_active ON public.offload_reason_rules USING btree (category_id, is_active);
CREATE INDEX idx_offrule_category_active ON public.offender_rules USING btree (category_id, is_active);
CREATE INDEX idx_offsess_cat_time ON public.offload_sessions USING btree (client_id, reason_category_id, started_at);
CREATE INDEX idx_offsess_client_time ON public.offload_sessions USING btree (client_id, started_at);
CREATE INDEX idx_offsess_import ON public.offload_sessions USING btree (offload_import_id);
CREATE INDEX idx_offsess_job_time ON public.offload_sessions USING btree (client_id, offload_job_id, started_at);
CREATE INDEX idx_offsess_result_time ON public.offload_sessions USING btree (client_id, result, started_at);
CREATE INDEX idx_pwd_history_user ON public.password_history USING btree (user_id);
CREATE INDEX idx_repocap_client_import ON public.repo_capacity_samples USING btree (client_id, disk_usage_import_id);
CREATE INDEX idx_repocap_import_tier ON public.repo_capacity_samples USING btree (disk_usage_import_id, tier);
CREATE INDEX idx_report_client ON public.report_sessions USING btree (client_id);
CREATE INDEX idx_report_client_date ON public.report_sessions USING btree (client_id, date_start, date_end);
CREATE INDEX idx_rpopol_active ON public.rpo_policies USING btree (is_active);
CREATE INDEX idx_security_log_time ON public.security_log USING btree (created_at);
CREATE INDEX idx_security_log_type ON public.security_log USING btree (event_type);
CREATE INDEX idx_upload_client ON public.upload_sessions USING btree (client_id);
CREATE INDEX idx_upload_status ON public.upload_sessions USING btree (status);
CREATE INDEX idx_user_clients_user ON public.user_clients USING btree (user_id);
CREATE INDEX idx_vmrpo_client_vm ON public.vm_rpo_assignments USING btree (client_id, vm_name);
CREATE UNIQUE INDEX uq_bkpsess_client_hash ON public.backup_vm_sessions USING btree (client_id, content_hash) WHERE (task_session_id IS NULL);
CREATE UNIQUE INDEX uq_bkpsess_client_task ON public.backup_vm_sessions USING btree (client_id, task_session_id) WHERE (task_session_id IS NOT NULL);
CREATE UNIQUE INDEX uq_diskpt_import_rp ON public.disk_backup_points USING btree (disk_usage_import_id, rp_id) WHERE (rp_id IS NOT NULL);
CREATE UNIQUE INDEX uq_jcobj_snap_objid ON public.job_config_objects USING btree (job_config_snapshot_id, object_id) WHERE (object_id IS NOT NULL);
CREATE UNIQUE INDEX uq_jcobj_snap_objname ON public.job_config_objects USING btree (job_config_snapshot_id, object_name) WHERE (object_id IS NULL);
CREATE UNIQUE INDEX uq_jcsnap_client_hash ON public.job_config_snapshots USING btree (client_id, content_hash) WHERE (job_id IS NULL);
CREATE UNIQUE INDEX uq_jcsnap_client_job_time ON public.job_config_snapshots USING btree (client_id, job_id, collected_at) WHERE (job_id IS NOT NULL);
CREATE UNIQUE INDEX uq_jobenv_client_job ON public.job_environments USING btree (client_id, job_name);
CREATE UNIQUE INDEX uq_offsess_client_hash ON public.offload_sessions USING btree (client_id, content_hash) WHERE (session_id IS NULL);
CREATE UNIQUE INDEX uq_offsess_client_session ON public.offload_sessions USING btree (client_id, session_id) WHERE (session_id IS NOT NULL);
ALTER TABLE ONLY public.backup_imports
    ADD CONSTRAINT backup_imports_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.backup_routines
    ADD CONSTRAINT backup_routines_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.backup_vm_sessions
    ADD CONSTRAINT backup_vm_sessions_backup_import_id_fkey FOREIGN KEY (backup_import_id) REFERENCES public.backup_imports(id);
ALTER TABLE ONLY public.backup_vm_sessions
    ADD CONSTRAINT backup_vm_sessions_backup_routine_id_fkey FOREIGN KEY (backup_routine_id) REFERENCES public.backup_routines(id);
ALTER TABLE ONLY public.backup_vm_sessions
    ADD CONSTRAINT backup_vm_sessions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.branding_profiles
    ADD CONSTRAINT branding_profiles_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.disk_backup_points
    ADD CONSTRAINT disk_backup_points_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.disk_backup_points
    ADD CONSTRAINT disk_backup_points_disk_usage_import_id_fkey FOREIGN KEY (disk_usage_import_id) REFERENCES public.disk_usage_imports(id);
ALTER TABLE ONLY public.disk_usage_imports
    ADD CONSTRAINT disk_usage_imports_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.imported_events
    ADD CONSTRAINT fk_ev_offender_cat FOREIGN KEY (offender_category_id) REFERENCES public.offender_categories(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.offload_sessions
    ADD CONSTRAINT fk_offsess_reason_cat FOREIGN KEY (reason_category_id) REFERENCES public.offload_reason_categories(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.offload_sessions
    ADD CONSTRAINT fk_offsess_reason_rule FOREIGN KEY (reason_rule_id) REFERENCES public.offload_reason_rules(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.imported_events
    ADD CONSTRAINT imported_events_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.imported_events
    ADD CONSTRAINT imported_events_upload_session_id_fkey FOREIGN KEY (upload_session_id) REFERENCES public.upload_sessions(id);
ALTER TABLE ONLY public.job_actions
    ADD CONSTRAINT job_actions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.job_config_imports
    ADD CONSTRAINT job_config_imports_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.job_config_objects
    ADD CONSTRAINT job_config_objects_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.job_config_objects
    ADD CONSTRAINT job_config_objects_job_config_snapshot_id_fkey FOREIGN KEY (job_config_snapshot_id) REFERENCES public.job_config_snapshots(id);
ALTER TABLE ONLY public.job_config_snapshots
    ADD CONSTRAINT job_config_snapshots_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.job_config_snapshots
    ADD CONSTRAINT job_config_snapshots_job_config_import_id_fkey FOREIGN KEY (job_config_import_id) REFERENCES public.job_config_imports(id);
ALTER TABLE ONLY public.job_environments
    ADD CONSTRAINT job_environments_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.job_summaries
    ADD CONSTRAINT job_summaries_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.report_sessions(id);
ALTER TABLE ONLY public.offender_rules
    ADD CONSTRAINT offender_rules_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.offender_categories(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.offload_imports
    ADD CONSTRAINT offload_imports_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.offload_jobs
    ADD CONSTRAINT offload_jobs_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.offload_reason_rules
    ADD CONSTRAINT offload_reason_rules_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.offload_reason_categories(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.offload_sessions
    ADD CONSTRAINT offload_sessions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.offload_sessions
    ADD CONSTRAINT offload_sessions_offload_import_id_fkey FOREIGN KEY (offload_import_id) REFERENCES public.offload_imports(id);
ALTER TABLE ONLY public.offload_sessions
    ADD CONSTRAINT offload_sessions_offload_job_id_fkey FOREIGN KEY (offload_job_id) REFERENCES public.offload_jobs(id);
ALTER TABLE ONLY public.password_history
    ADD CONSTRAINT password_history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);
ALTER TABLE ONLY public.repo_capacity_samples
    ADD CONSTRAINT repo_capacity_samples_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.repo_capacity_samples
    ADD CONSTRAINT repo_capacity_samples_disk_usage_import_id_fkey FOREIGN KEY (disk_usage_import_id) REFERENCES public.disk_usage_imports(id);
ALTER TABLE ONLY public.report_sessions
    ADD CONSTRAINT report_sessions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.upload_sessions
    ADD CONSTRAINT upload_sessions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.user_clients
    ADD CONSTRAINT user_clients_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.user_clients
    ADD CONSTRAINT user_clients_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.vm_rpo_assignments
    ADD CONSTRAINT vm_rpo_assignments_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.vm_rpo_assignments
    ADD CONSTRAINT vm_rpo_assignments_rpo_policy_id_fkey FOREIGN KEY (rpo_policy_id) REFERENCES public.rpo_policies(id) ON DELETE SET NULL;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SCHEMA_DDL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    )
