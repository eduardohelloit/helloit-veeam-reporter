#!/usr/bin/env python3
"""
Importador CLI de arquivos NDJSON de Offload (Capacity Tier).

Lê um arquivo gerado pelo coletor PowerShell e alimenta a base histórica
(offload_imports / offload_jobs / offload_sessions), de forma idempotente
(reimportar o mesmo arquivo não duplica sessões).

USO (dentro do container web ou no host com DATABASE_URL apontando ao Postgres):

    python scripts/import_offload.py --client "Empresa Exemplo" --file offload/offload_AAAAMMDD_HHMMSS.ndjson
    python scripts/import_offload.py --client-id 1   --file /caminho/arquivo.ndjson
    python scripts/import_offload.py --client "Empresa Exemplo" --dir offload/        # importa todos os .ndjson da pasta
    python scripts/import_offload.py --list-clients

Se --client (por nome) não existir, use --create-client para criá-lo.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

# Permite rodar como "python scripts/import_offload.py" a partir da raiz do projeto
sys.path.insert(0, ".")

from app.database import SessionLocal  # noqa: E402
from app.models import Client          # noqa: E402
from app.services.offload_service import ingest_ndjson  # noqa: E402


def _resolve_client(db, *, client_id, client_name, create_client):
    if client_id is not None:
        client = db.query(Client).get(client_id)
        if not client:
            sys.exit(f"[ERRO] Cliente id={client_id} não encontrado.")
        return client

    if client_name:
        client = db.query(Client).filter(Client.name == client_name).first()
        if client:
            return client
        if create_client:
            client = Client(name=client_name, is_active=True)
            db.add(client)
            db.commit()
            db.refresh(client)
            print(f"[OK] Cliente '{client_name}' criado (id={client.id}).")
            return client
        sys.exit(
            f"[ERRO] Cliente '{client_name}' não existe. "
            f"Use --create-client para criá-lo ou --list-clients para ver os existentes."
        )

    sys.exit("[ERRO] Informe --client <nome> ou --client-id <id>.")


def _print_summary(filename: str, s: dict) -> None:
    print(f"\n== Resumo da importação: {os.path.basename(filename)} ==")
    print(f"  Import ID:            {s['import_id']}  ({s['status']})")
    print(f"  Linhas totais:        {s['total_lines']}")
    print(f"  Sessões inseridas:    {s['inserted_sessions']}")
    print(f"  Duplicadas ignoradas: {s['duplicate_sessions']}")
    print(f"  Linhas ignoradas:     {s['ignored_lines']}  (sem campos mínimos)")
    print(f"  Linhas com erro:      {s['failed_lines']}  (parsing)")
    print(f"  Success/Warning/Failed: {s['success_count']} / {s['warning_count']} / {s['failed_count']}")
    print(f"  Período:              {s['min_started_at']} → {s['max_started_at']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Importa NDJSON de Offload do Veeam.")
    ap.add_argument("--client",        help="Nome do cliente")
    ap.add_argument("--client-id",     type=int, help="ID do cliente")
    ap.add_argument("--file",          help="Caminho do arquivo .ndjson")
    ap.add_argument("--dir",           help="Pasta com arquivos .ndjson (importa todos)")
    ap.add_argument("--create-client", action="store_true", help="Cria o cliente se não existir")
    ap.add_argument("--list-clients",  action="store_true", help="Lista clientes e sai")
    ap.add_argument("--by",            default="cli", help="Identificação de quem importou (uploaded_by)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.list_clients:
            clients = db.query(Client).order_by(Client.id).all()
            if not clients:
                print("(nenhum cliente cadastrado)")
            for c in clients:
                flag = "" if c.is_active else " [inativo]"
                print(f"  {c.id:>4}  {c.name}{flag}")
            return

        files: list[str] = []
        if args.dir:
            files = sorted(glob.glob(os.path.join(args.dir, "*.ndjson")))
            if not files:
                sys.exit(f"[ERRO] Nenhum .ndjson encontrado em {args.dir}")
        elif args.file:
            if not os.path.isfile(args.file):
                sys.exit(f"[ERRO] Arquivo não encontrado: {args.file}")
            files = [args.file]
        else:
            sys.exit("[ERRO] Informe --file <arquivo> ou --dir <pasta> (ou --list-clients).")

        client = _resolve_client(
            db,
            client_id=args.client_id,
            client_name=args.client,
            create_client=args.create_client,
        )
        print(f"[OK] Cliente: {client.name} (id={client.id})")

        for f in files:
            summary = ingest_ndjson(
                db,
                client_id=client.id,
                file_path=f,
                original_filename=os.path.basename(f),
                uploaded_by=args.by,
            )
            _print_summary(f, summary)
    finally:
        db.close()


if __name__ == "__main__":
    main()
