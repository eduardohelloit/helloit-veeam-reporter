# Deploy em VM de 1 GB (Oracle Cloud / AWS free tier)

Perfil testado: **Ubuntu 24.04 x86_64, 1 GB RAM, 2 vCPU, 4 GB swap**
(Oracle Cloud `VM.Standard.E2.1.Micro`).

## Por que este perfil precisa de cuidado

Parado, PostgreSQL + app + SO já beiram 1 GB. Sem ajustes, o build e as
importações levam OOM-kill. Dois pilares resolvem:

1. **Swap** (≥ 2 GB; a OCI já provisiona 4 GB). Cobre build e picos.
2. **`docker-compose.override.yml`** deste diretório: Postgres frugal + teto de
   RAM por container com folga de swap.

## Passos

```bash
# 1) Docker Engine + Compose
curl -fsSL https://get.docker.com | sudo sh

# 2) swap de 4 GB (a OCI já traz; só garantir)
sudo swapon --show || (sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile \
  && sudo mkswap /swapfile && sudo swapon /swapfile \
  && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab)

# 3) projeto na VM + override de 1 GB
cd veeam-weekly-reporter
cp deploy/oci-1gb/docker-compose.override.yml ./docker-compose.override.yml

# 4) .env com segredos fortes (POSTGRES_PASSWORD, ADMIN_*, SESSION_SECRET)

# 5) subir (banco nasce zerado; migrações e admin rodam no start)
sudo docker compose up -d --build
```

## Firewall — DOIS níveis na OCI

A porta 443 precisa ser liberada em **dois** lugares:

- **Host (iptables):** a imagem Ubuntu da OCI vem com `INPUT` restritivo.
  ```bash
  sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save
  ```
- **Nuvem (Security List / NSG):** adicionar regra de ingresso TCP 443 no
  console da OCI (Networking → VCN → Security List). **Isso não dá para fazer
  por SSH** — tem de ser no painel da Oracle.

## Verificação

```bash
sudo docker compose exec -T web alembic current   # deve mostrar o head
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost/login   # 200
free -h                                            # swap em uso é normal
```

## Acesso externo via Cloudflare Tunnel + ZTNA (Access)

Alternativa à Security List: em vez de abrir 443, um **Cloudflare Tunnel**
(serviço `cloudflared` no `docker-compose.override.yml`) faz conexão
**outbound-only** para a Cloudflare — nada é exposto na OCI. Uma camada
**Cloudflare Access** coloca autenticação de identidade (ZTNA) antes do login.

Custo: `cloudflared` ~25 MB RAM; Zero Trust grátis até 50 usuários.

**Painel Cloudflare (Zero Trust):**
1. **Networks → Tunnels → Create a tunnel** → conector *Cloudflared* → nomeie →
   copie o **token** (string após `--token`).
2. Grave na VM em `.env`: `TUNNEL_TOKEN=<token>` (chmod 600) e suba:
   `sudo docker compose up -d cloudflared`.
   O conector fica **HEALTHY** (log: "Registered tunnel connection").
3. **Public Hostname**: `sub.dominio` → Type `HTTPS` → URL `web:8443` →
   *Origin request → No TLS Verify: ON* (o cert do app é self-signed; a
   Cloudflare entrega um cert válido ao usuário final). Save.
4. **Access controls → Applications → Add → Self-hosted** no mesmo hostname,
   com política *Allow* incluindo os e-mails autorizados (ex.: e-mails
   terminando em `@suaempresa.com.br`). Isso é o ZTNA na frente do login.

Sem o passo 4, o hostname fica acessível na internet (só o login do app
protege). Com ele, o usuário passa pela identidade da Cloudflare antes.

O `TUNNEL_TOKEN` nunca vai para o repositório — só para o `.env` da VM.
