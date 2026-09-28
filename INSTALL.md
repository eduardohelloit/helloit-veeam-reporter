# Instalação — HelloIT Veeam Reporter

## Pré-requisitos (Ubuntu 22.04+)
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"   # relogar após este comando
```

## Passos
1. Extraia o pacote e entre na pasta:
   ```bash
   tar xzf helloit-veeam-reporter-*.tar.gz && cd helloit-veeam-reporter
   ```
2. Crie o `.env` a partir do exemplo e defina senhas fortes:
   ```bash
   cp .env.example .env && chmod 600 .env
   nano .env      # POSTGRES_PASSWORD (obrigatório) e ADMIN_PASSWORD
   ```
3. Suba os containers (banco criado vazio; migrações rodam sozinhas):
   ```bash
   sudo docker compose build
   sudo docker compose up -d
   ```
4. Acesse `https://SEU_SERVIDOR/` (certificado self-signed).
   - Sem `ADMIN_PASSWORD` definido, um admin é criado com senha temporária e **troca obrigatória** no 1º acesso.
     A senha temporária aparece nos logs: `sudo docker compose logs web | grep "Senha temporária"`
5. Configure a identidade visual em **Administração → Identidade Visual**.

## Disco de dados dedicado (opcional, recomendado em produção)
Para manter os dados pesados (Postgres + uploads) fora do disco do SO, monte um disco
dedicado em `/var/lib/docker` **antes** de instalar o Docker:
```bash
sudo mkfs.ext4 -L dockerdata /dev/sdX1
echo "LABEL=dockerdata /var/lib/docker ext4 defaults,noatime,nofail 0 2" | sudo tee -a /etc/fstab
sudo mkdir -p /var/lib/docker && sudo mount -a
```

## Atualização de versão
```bash
sudo docker compose build web && sudo docker compose up -d web
```
Dados ficam em volumes nomeados (não se perdem no rebuild).

## Backup
`scripts/backup.sh` gera um arquivo único cifrado (AES-256) com dump do banco +
volumes + código. Defina a senha em `/etc/veeam-backup.pass` (chmod 600) e agende via cron.
