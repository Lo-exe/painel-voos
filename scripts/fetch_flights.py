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
from datetime import date, datetime, timezone

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
    # Alguns WAFs/CDNs de APIs públicas (a SIROS roda atrás de Cloudflare)
    # filtram requisições com o User-Agent padrão do requests
    # ("python-requests/x.x"), especialmente vindas de IPs de datacenter
    # como os runners do GitHub Actions, retornando 200 OK com corpo vazio
    # em vez de um erro explícito. Um User-Agent de navegador evita isso.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"ERRO: falha ao consultar a API SIROS: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Diagnóstico: status={response.status_code}, tamanho_resposta={len(response.text)} bytes")

    try:
        payload = response.json()
    except ValueError as exc:
        print(f"ERRO: resposta da API SIROS não é um JSON válido: {exc}", file=sys.stderr)
        print(f"Diagnóstico: primeiros 300 caracteres da resposta: {response.text[:300]!r}", file=sys.stderr)
        sys.exit(1)

    # A API SIROS devolve o corpo como uma STRING contendo JSON (JSON
    # duplamente codificado), não como uma lista/dict direto. Se o primeiro
    # response.json() já desserializou para uma string, fazemos um segundo
    # json.loads() nela para chegar na lista de voos de verdade.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError) as exc:
            print(f"ERRO: não foi possível decodificar o JSON aninhado da API SIROS: {exc}", file=sys.stderr)
            sys.exit(1)

    # A API pode retornar a lista diretamente ou dentro de uma chave "data"/"voos"
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "voos", "registros"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    print(f"Diagnóstico: formato de payload inesperado após decodificação: {type(payload)}")
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
    """Remove voos duplicados com base em (companhia, numero_voo, etapa).

    O número do voo sozinho NÃO identifica um voo de forma única: companhias
    diferentes reutilizam os mesmos números, e um voo com escala usa o mesmo
    número em cada etapa (nr_etapa). Por isso a chave de deduplicação usa
    companhia + número + etapa — a mesma combinação da constraint UNIQUE do
    banco (uq_flight_dedupe), para nunca violar o upsert.
    """
    seen = set()
    unique_flights = []
    for flight in flights:
        airline = flight.get("sg_empresa_icao") or flight.get("nm_empresa") or ""
        flight_number = flight.get("nr_voo") or flight.get("numero_voo") or ""
        leg_number = flight.get("nr_etapa") or ""
        dedupe_key = (str(airline).strip(), str(flight_number).strip(), str(leg_number).strip())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique_flights.append(flight)
    return unique_flights


def parse_siros_datetime(value):
    """Converte 'DD/MM/AAAA HH:MM' (formato da API SIROS, em UTC) para ISO 8601.

    Retorna None se o valor estiver ausente ou em formato inesperado, em vez
    de falhar o script inteiro por causa de um único voo com data estranha.
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value).strip(), "%d/%m/%Y %H:%M")
        return parsed.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


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
                # A API SIROS não traz nome de companhia, só o código ICAO dela
                "airline": flight.get("sg_empresa_icao") or flight.get("nm_empresa") or None,
                # Etapa/escala do voo — necessária para identificar de forma única
                # um voo com o mesmo número em trechos diferentes no mesmo dia
                "leg_number": str(flight.get("nr_etapa") or "") or None,
                "origin_icao": str(origin).upper() or None,
                "destination_icao": str(destination).upper() or None,
                # Campos reais da API vêm com sufixo _utc, no formato DD/MM/AAAA HH:MM
                "scheduled_departure": parse_siros_datetime(
                    flight.get("dt_partida_prevista_utc") or flight.get("dt_partida_prevista")
                ),
                "scheduled_arrival": parse_siros_datetime(
                    flight.get("dt_chegada_prevista_utc") or flight.get("dt_chegada_prevista")
                ),
                # A API não expõe status de voo em tempo real; usamos o tipo de
                # serviço (ex.: "REGULAR DE PASSAGEIROS DOMÉSTICA") como aproximação
                "status": flight.get("ds_tipo_servico") or flight.get("situacao_voo") or flight.get("status") or None,
                "aircraft_type": flight.get("sg_equipamento_icao") or flight.get("tp_aeronave") or flight.get("aeronave") or None,
                "reference_date": reference_date_iso,
            }
        )
    return rows


def send_to_supabase(rows: list, supabase_url: str, service_key: str) -> None:
    """Envia (upsert) os voos para a tabela `flights` via PostgREST."""
    if not rows:
        print("Nenhum voo para enviar após filtro/deduplicação.")
        return

    # A PK da tabela é `id` (UUID gerado automaticamente), então o upsert
    # precisa dizer explicitamente qual constraint usar para detectar
    # conflito — senão o PostgREST tenta resolver pela PK (que nunca colide,
    # já que é sempre um UUID novo) e um INSERT simples acaba violando a
    # constraint uq_flight_dedupe.
    endpoint = (
        f"{supabase_url.rstrip('/')}/rest/v1/flights"
        "?on_conflict=airline,flight_number,leg_number,reference_date"
    )
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
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
