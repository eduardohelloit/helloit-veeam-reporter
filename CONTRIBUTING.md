# Contribuindo

Obrigado pelo interesse! 🎉

## Como contribuir
1. Faça um **fork** e crie um branch: `git checkout -b minha-feature`
2. Rode localmente (veja [INSTALL.md](INSTALL.md)).
3. Faça commits claros e abra um **Pull Request** descrevendo a mudança.

## Reportando bugs / ideias
Abra uma **Issue** descrevendo o problema (passos para reproduzir) ou a sugestão.

## Padrões
- Python 3.11, FastAPI, SQLAlchemy 2. Mantenha o estilo do código existente.
- Alterações de schema → nova migration Alembic (`alembic/versions/`).
- Nunca versione segredos (`.env`), dados de cliente ou uploads.
