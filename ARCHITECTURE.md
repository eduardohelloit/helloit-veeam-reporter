# Arquitetura — HelloIT Veeam Reporter

## Visão geral
```
Coletores PowerShell (no VBR)  ─┐
Upload manual (.evtx/.csv/.xml) ─┼─►  Parser  ─►  PostgreSQL  ─►  Telas + Relatórios
NDJSON (offload/job-config/disk)─┘        (multi-tenant por client_id)
```

## Containers (docker-compose.yml)
| Serviço | Imagem | Porta | Função |
|---|---|---|---|
| `db`  | `postgres:16-alpine` | 5432 (interna) | Banco `veeam_reporter` |
| `web` | build local (`Dockerfile`) | `443 → 8443` | App FastAPI/Uvicorn, HTTPS |

Hardening do `web`: `read_only`, `tmpfs /tmp`, `no-new-privileges`, `cap_drop: ALL`,
`pids_limit`, `mem_limit`. Boot (`start.sh`) gera cert TLS e `session.key` se não existirem.

## Volumes
`veeam_db` (Postgres) · `veeam_uploads` (`/data/uploads`, arquivos crus + NDJSON) ·
`veeam_reports` (`/data/reports`) · `veeam_branding` (`/data/branding`) ·
`veeam_certs` (`/data/certs`, auto-gerado).
Em produção, montar `/var/lib/docker` em disco dedicado mantém o dado pesado fora do SO.

## Modelo de dados (multi-tenant)
Isolamento por **`client_id`** (FK → `clients`). Domínios: núcleo/auth
(`clients`, `users`, `user_clients`, políticas de senha, `security_log`); ingestão
(`upload_sessions`, `imported_events`); backup (`backup_imports → backup_routines →
backup_vm_sessions`, `report_sessions → job_summaries`); offload (`offload_imports →
offload_jobs → offload_sessions`); auditoria de config (`job_config_imports →
job_config_snapshots → job_config_objects`, `job_environments`); disco
(`disk_usage_imports → disk_backup_points`, `repo_capacity_samples`); RPO/classificação
(`rpo_policies`, `vm_rpo_assignments`, `offender_*`); `branding_profiles` (por cliente;
`client_id` nulo = padrão global). Schema versionado por Alembic.

## Módulos da aplicação (`app/`)
- `main.py` — FastAPI, monta os routers, auth por sessão, rate-limit.
- `models.py` — todas as tabelas (SQLAlchemy 2).
- `routers/` — por domínio, em pares *admin* (ingestão/gestão) e *analytics* (telas):
  backup, offload, job_config, disk (+ disk_growth), block_cloning, env_report,
  vm_missing, ps_import, auth_admin.
- `services/` — geração de relatórios (`report/excel/docx/pptx_service`), regras de
  negócio (`offload/backup_perf/disk_usage/job_config_service`), classificação,
  branding, detecção de formato.
- `parser/` — `evtx`, `csv`, `xml`.

## Coletores (`scripts/collectors/`)
Rodam no VBR (somente leitura) e geram NDJSON — o REST do Veeam não expõe tudo.
`collect_all.ps1` orquestra os demais. Ingestão via rota `ps_import`.
Recomendação: coletar periodicamente para acumular histórico de longo prazo.

## Operação
```bash
docker compose logs -f web                # logs
docker compose exec db psql -U veeam -d veeam_reporter   # console SQL
docker compose build web && docker compose up -d web     # atualizar código
```
