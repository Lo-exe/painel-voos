# Painel de Voos — SN-2026

Painel web que exibe voos coletados da API pública **SIROS/ANAC** (Sistema de Registros dos Serviços Aéreos), filtrados por aeroporto (ICAO) e armazenados em um banco **Supabase (Postgres)**. Publicado automaticamente no **GitHub Pages** via GitHub Actions.

## Arquitetura

```
sql/setup.sql                        -> schema do Supabase (tabela, RLS, GRANTs, constraint)
scripts/fetch_flights.py             -> busca na API SIROS, filtra, deduplica e envia ao Supabase
data/airports.json                   -> lista dos aeroportos exibidos no filtro do painel
index.html                           -> painel estático que lê os voos direto do Supabase
.github/workflows/update_flights.yml -> roda o script periodicamente e publica o index.html
```

## Pré-requisitos

- Conta no [Supabase](https://supabase.com) (gratuita)
- Repositório no GitHub com GitHub Pages habilitado
- Python 3.12+ com `pip install requests` (para rodar localmente)

## 1. Configurar o Supabase

1. Crie um novo projeto em supabase.com.
2. Abra o **SQL Editor** e rode o conteúdo de `sql/setup.sql`.
3. Em **Project Settings → API**, copie:
   - `Project URL` → variável `SUPABASE_URL`
   - `service_role` key → segredo `SUPABASE_SERVICE_KEY` (nunca exponha no frontend)
   - `anon` key → cole em `index.html` na constante `SUPABASE_ANON_KEY`

## 2. Configurar o repositório GitHub

```bash
gh secret set SUPABASE_URL --body "https://yrzrmseokymaokcnuzlb.supabase.co"
gh secret set SUPABASE_SERVICE_KEY --body "SUA_SERVICE_ROLE_KEY"
gh variable set AIRPORTS --body "SBCA,SBCT,SBGR,SBSP,SBGL,SBBR,SBFL,SBPA"
```

Habilite o GitHub Pages em **Settings → Pages → Source: GitHub Actions**.

## 3. Rodar localmente

```bash
export SUPABASE_URL=https://yrzrmseokymaokcnuzlb.supabase.co
export SUPABASE_SERVICE_KEY=SUAKEY
export AIRPORTS=SBCA
python scripts/fetch_flights.py
```

## 4. Publicação automática

O workflow `.github/workflows/update_flights.yml` roda a cada 30 minutos (e a cada push em `main`):

1. Job `fetch-and-insert` executa `scripts/fetch_flights.py` e popula o Supabase.
2. Job `deploy-pages` publica `index.html` (e `data/airports.json`) no GitHub Pages.

## Segurança

- A tabela `flights` tem **Row Level Security** ativado: o frontend (chave `anon`) só tem permissão de `SELECT`; apenas a `service_role` (usada pelo GitHub Actions, nunca exposta no browser) pode inserir/atualizar dados.
- `index.html` usa `escapeHtml()` em todo texto vindo da API antes de inserir via `innerHTML`, para evitar injeção de HTML/script.

## Fonte dos dados

API pública da ANAC: `GET https://sas.anac.gov.br/sas/siros_api/api/voos?dataReferencia=DDMMAAAA` — sem autenticação, sem custo, sem limite de uso documentado.
