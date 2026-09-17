#!/usr/bin/env python3
"""
fetch_flights.py
=================
Busca voos do dia na API pública SIROS/ANAC (Sistema de Registros dos
Serviços Aéreos), filtra pelos aeroportos configurados em AIRPORTS,
remove duplicados e envia o resultado para uma tabela `flights` no
Supabase (Postgres) via API REST (PostgREST).

Variáveis de ambiente esperadas:
    SUPABASE_URL          - ex.: https://SEU-PROJETO.supabase.co
    SUPABASE_SERVICE_KEY  - service_role key do projeto Supabase
    AIRPORTS              - códigos ICAO separados por vírgula
                             ex.: "SBCA" ou "SBCA,SBCT,SBGR"

Uso local:
    SUPABASE_URL=https://SEU.supabase.co \
    SUPABASE_SERVICE_KEY=SUAKEY \
    AIRPORTS=SBCA \
    python scripts/fetch_flights.py
"""

import json
import os
import sys
from datetime import date

import requests

SIROS_BASE_URL = "https://sas.anac.gov.br/sas/siros_api/api"
REQUEST_TIMEOUT_SECONDS = 30


def get_required_env(name: str) -> str:
    """Lê uma variável de ambiente obrigatória ou encerra o script."""
    value = os.environ.get(name)
    if not value:
        print(f"ERRO: variável de ambiente obrigatória ausente: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def build_reference_date() -> str:
    """Retorna a data de hoje no formato DDMMAAAA esperado pela API SIROS."""
    return date.today().strftime("%d%m%Y")


def fetch_flights_from_siros(reference_date: str) -> list:
    """Busca todos os voos do dia na API SIROS/ANAC."""
    url = f"{SIROS_BASE_URL}/voos"
    params = {"dataReferencia": reference_date}

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"ERRO: falha ao consultar a API SIROS: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        payload = response.json()
    except ValueError as exc:
        print(f"ERRO: resposta da API SIROS não é um JSON válido: {exc}", file=sys.stderr)
        sys.exit(1)

    # A API pode retornar a lista diretamente ou dentro de uma chave "data"/"voos"
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "voos", "registros"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    return []


def filter_by_airports(flights: list, airports: list) -> list:
    """Mantém apenas voos cujo aeroporto de origem OU destino está na lista configurada."""
    airports_upper = {a.strip().upper() for a in airports if a.strip()}
    filtered = []
    for flight in flights:
        origin = str(flight.get("sg_icao_origem") or flight.get("origem_icao") or "").upper()
        destination = str(flight.get("sg_icao_destino") or flight.get("destino_icao") or "").upper()
        if origin in airports_upper or destination in airports_upper:
            filtered.append(flight)
    return filtered


def deduplicar_voos(flights: list) -> list:
    """Remove voos duplicados com base em (numero_voo, origem, destino).

    A própria API SIROS pode repetir o mesmo voo em registros diferentes
    (ex.: atualização de status). Mantemos apenas a primeira ocorrência
    de cada combinação para evitar violar a constraint UNIQUE do banco.
    """
    seen = set()
    unique_flights = []
    for flight in flights:
        flight_number = flight.get("nr_voo") or flight.get("numero_voo") or ""
        origin = flight.get("sg_icao_origem") or flight.get("origem_icao") or ""
        destination = flight.get("sg_icao_destino") or flight.get("destino_icao") or ""
        dedupe_key = (str(flight_number).strip(), str(origin).strip(), str(destination).strip())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique_flights.append(flight)
    return unique_flights


def to_supabase_rows(flights: list, reference_date_iso: str) -> list:
    """Converte os registros da API SIROS para o formato da tabela `flights`."""
    rows = []
    for flight in flights:
        origin = flight.get("sg_icao_origem") or flight.get("origem_icao") or ""
        destination = flight.get("sg_icao_destino") or flight.get("destino_icao") or ""
        # icao de referência: prioriza a origem, cai para o destino
        reference_icao = str(origin).upper() or str(destination).upper()
        rows.append(
            {
                "icao": reference_icao,
                "flight_number": str(flight.get("nr_voo") or flight.get("numero_voo") or ""),
                "airline": flight.get("nm_empresa") or flight.get("empresa") or None,
                "origin_icao": str(origin).upper() or None,
                "destination_icao": str(destination).upper() or None,
                "scheduled_departure": flight.get("dt_partida_prevista") or None,
                "scheduled_arrival": flight.get("dt_chegada_prevista") or None,
                "status": flight.get("situacao_voo") or flight.get("status") or None,
                "aircraft_type": flight.get("tp_aeronave") or flight.get("aeronave") or None,
                "reference_date": reference_date_iso,
            }
        )
    return rows


def send_to_supabase(rows: list, supabase_url: str, service_key: str) -> None:
    """Envia (upsert) os voos para a tabela `flights` via PostgREST."""
    if not rows:
        print("Nenhum voo para enviar após filtro/deduplicação.")
        return

    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/flights"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        # upsert baseado na constraint uq_flight_dedupe (icao, flight_number, reference_date)
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    try:
        response = requests.post(endpoint, headers=headers, data=json.dumps(rows), timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        body = getattr(exc.response, "text", "")
        print(f"ERRO: falha ao enviar dados para o Supabase: {exc} | {body}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: {len(rows)} voo(s) enviados ao Supabase.")


def main() -> None:
    supabase_url = get_required_env("SUPABASE_URL")
    service_key = get_required_env("SUPABASE_SERVICE_KEY")
    airports_raw = get_required_env("AIRPORTS")
    airports = airports_raw.split(",")

    reference_date = build_reference_date()
    reference_date_iso = date.today().isoformat()

    print(f"Consultando SIROS para a data {reference_date}, aeroportos: {airports}")
    all_flights = fetch_flights_from_siros(reference_date)
    print(f"Total retornado pela API SIROS: {len(all_flights)}")

    airport_flights = filter_by_airports(all_flights, airports)
    print(f"Filtrados para o(s) ICAO configurado(s): {len(airport_flights)}")

    unique_flights = deduplicar_voos(airport_flights)
    print(f"Duplicados removidos: {len(airport_flights) - len(unique_flights)}")

    rows = to_supabase_rows(unique_flights, reference_date_iso)
    send_to_supabase(rows, supabase_url, service_key)
    print("Status final: concluido")


if __name__ == "__main__":
    main()
