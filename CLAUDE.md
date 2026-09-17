# CLAUDE.md — Painel de Voos (SN-2026)

## Stack
- Backend de dados: Supabase (Postgres + PostgREST)
- Coleta: Python 3.12, biblioteca `requests`
- Frontend: HTML/CSS/JS puro (sem framework), consumindo a API REST do Supabase direto do navegador
- CI/CD: GitHub Actions (`.github/workflows/update_flights.yml`) + GitHub Pages

## Fonte de dados
API pública da ANAC (SIROS): `https://sas.anac.gov.br/sas/siros_api/api/voos?dataReferencia=DDMMAAAA`
Sem autenticação, sem custo, sem chave de API.

## Variáveis de ambiente / segredos
- `SUPABASE_URL` — URL do projeto Supabase (não sensível)
- `SUPABASE_SERVICE_KEY` — service_role / secret key do Supabase (sensível, nunca commitar, nunca colar em chat)
- `AIRPORTS` — lista de ICAO separada por vírgula (ex.: `SBCA,SBCT,SBGR`), configurada como GitHub Actions *variable* (não secret)

## Restrições
- Nunca commitar chaves/segredos no código ou no histórico do git.
- A chave usada no `index.html` (frontend) deve ser sempre a `anon`/`publishable key`, nunca a `service_role`/`secret key`.
- Toda alteração em `sql/setup.sql` deve manter o Row Level Security ativo na tabela `flights`.
- Respostas e comentários de código sempre em português do Brasil (pt-BR).
- Mudanças no `fetch_flights.py` devem manter a função de deduplicação e o tratamento de erro com `sys.exit(1)`.
