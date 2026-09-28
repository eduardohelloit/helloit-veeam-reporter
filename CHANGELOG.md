# Changelog — HelloIT Veeam Reporter

## [1.0.0] — Release inicial

Plataforma web para monitoramento e auditoria de backups Veeam.

### Módulos
- **Relatórios (Event Viewer)**: visão geral, status das rotinas, semanal/mensal, incidentes e ofensores, exportação PPTX/Excel.
- **Proteção de VMs / RPO**: cobertura por VM, RPO observado e políticas.
- **Servidores Duplicados**: identifica VMs protegidas por mais de uma rotina.
- **Offload / Capacity Tier**: ingestão e análise de sessões de offload (SOBR).
- **Performance de Backup**: duração/velocidade por VM, backups lentos e incidentes.
- **Auditoria de Configuração de Rotinas**: schedule, repositório, imutabilidade, proxies, application-aware, SQL log, retenção/GFS, com histórico de alterações (config drift).

### Infra
- FastAPI + PostgreSQL 16 + Docker Compose, TLS, hardening de container.
- Multi-tenant e identidade visual configurável por perfil.
- Coletores PowerShell (somente leitura) que geram NDJSON para importação.
