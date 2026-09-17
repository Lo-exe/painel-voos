-- ============================================================
-- Painel de Voos — SN-2026 / Ambiente de Desenvolvimento Integrado
-- Schema Supabase (PostgreSQL) para a tabela de voos
-- ============================================================

-- Extensão usada para gerar UUID como chave primária
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------
-- Tabela principal de voos
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flights (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    icao                TEXT NOT NULL,
    flight_number       TEXT NOT NULL,
    airline             TEXT,
    origin_icao         TEXT,
    destination_icao    TEXT,
    scheduled_departure  TIMESTAMPTZ,
    scheduled_arrival    TIMESTAMPTZ,
    status              TEXT,
    aircraft_type       TEXT,
    reference_date      DATE NOT NULL,
    inserted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Constraint de deduplicação: mesmo voo, mesma data de referência
    -- e mesmo aeroporto não pode ser inserido duas vezes
    CONSTRAINT uq_flight_dedupe UNIQUE (icao, flight_number, reference_date)
);

-- ------------------------------------------------------------
-- Índices para consulta rápida no painel (por aeroporto e data)
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_flights_icao ON flights (icao);
CREATE INDEX IF NOT EXISTS idx_flights_reference_date ON flights (reference_date);
CREATE INDEX IF NOT EXISTS idx_flights_icao_date ON flights (icao, reference_date);

-- ------------------------------------------------------------
-- Row Level Security
-- ------------------------------------------------------------
ALTER TABLE flights ENABLE ROW LEVEL SECURITY;

-- Leitura pública: qualquer pessoa (inclusive anon key no index.html)
-- pode ler os voos para exibir no painel.
CREATE POLICY "Permitir leitura publica dos voos"
    ON flights
    FOR SELECT
    TO anon, authenticated
    USING (true);

-- Escrita restrita: somente a service_role (usada pelo script
-- fetch_flights.py rodando no GitHub Actions) pode inserir/atualizar.
CREATE POLICY "Permitir escrita apenas via service_role"
    ON flights
    FOR INSERT
    TO service_role
    WITH CHECK (true);

CREATE POLICY "Permitir update apenas via service_role"
    ON flights
    FOR UPDATE
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ------------------------------------------------------------
-- GRANTs — privilégios mínimos por papel
-- ------------------------------------------------------------
-- anon (chave pública usada no index.html): apenas SELECT
GRANT SELECT ON flights TO anon;

-- authenticated: apenas SELECT (mesmo comportamento do anon aqui)
GRANT SELECT ON flights TO authenticated;

-- service_role (chave secreta usada no script do GitHub Actions):
-- acesso completo para popular a tabela
GRANT ALL ON flights TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- ------------------------------------------------------------
-- Comentários de documentação (facilita leitura no Supabase Studio)
-- ------------------------------------------------------------
COMMENT ON TABLE flights IS 'Voos coletados da API SIROS/ANAC, filtrados pelos aeroportos configurados em data/airports.json';
COMMENT ON COLUMN flights.icao IS 'Código ICAO do aeroporto de referência da consulta (ex.: SBCA)';
COMMENT ON CONSTRAINT uq_flight_dedupe ON flights IS 'Evita duplicar o mesmo voo quando o script roda mais de uma vez no mesmo dia';
