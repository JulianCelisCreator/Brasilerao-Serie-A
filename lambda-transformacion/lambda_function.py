"""
Lambda de transformacion - Pipeline Brasileirao (capas silver y gold).

Lee el JSON crudo de la capa bronze, lo limpia y estandariza (silver),
y construye el modelo analitico (gold) incluyendo el modelo predictivo
de Poisson descrito en la propuesta (seccion 3.4).

Entrada  : s3://<bucket>/bronze/<entidad>/fecha=AAAA-MM-DD/<entidad>.json
Silver   : s3://<bucket>/silver/<tabla>/  (Parquet)
Gold     : s3://<bucket>/gold/<tabla>/    (Parquet)

Tablas gold (seccion 3.3):
    - fact_partidos   : un registro por partido jugado
    - dim_equipos     : catalogo de equipos
    - fuerza_equipos  : fuerza ofensiva/defensiva local y visitante
    - predicciones    : P(victoria/empate/derrota) y puntos esperados (Poisson)

Usa la capa (layer) de AWS SDK for pandas (awswrangler), que incluye
pandas, numpy y pyarrow.
"""

import json
import logging
import os
from datetime import datetime, timezone
from math import exp, factorial

import boto3
import pandas as pd

try:
    import awswrangler as wr
except ImportError:  # permite pruebas locales de la logica sin awswrangler
    wr = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_BUCKET", "brasileirao-datalake-851725543887")
SEASON = os.environ.get("SEASON", "2024")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
MAX_GOLES = int(os.environ.get("MAX_GOLES", "10"))  # tope de goles para la matriz Poisson

s3 = boto3.client("s3", region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Utilidades de lectura
# ---------------------------------------------------------------------------
def leer_json_bronze(entidad, fecha):
    """Lee el JSON crudo de una entidad desde la capa bronze."""
    key = f"bronze/{entidad}/fecha={fecha}/{entidad}.json"
    logger.info("Leyendo s3://%s/%s", S3_BUCKET, key)
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def fecha_mas_reciente(entidad):
    """Encuentra la particion de fecha mas reciente para una entidad en bronze."""
    prefix = f"bronze/{entidad}/"
    paginator = s3.get_paginator("list_objects_v2")
    fechas = set()
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for item in page.get("Contents", []):
            # key: bronze/<entidad>/fecha=AAAA-MM-DD/<entidad>.json
            partes = item["Key"].split("/")
            for p in partes:
                if p.startswith("fecha="):
                    fechas.add(p.replace("fecha=", ""))
    if not fechas:
        raise FileNotFoundError(f"No hay datos en bronze para {entidad}")
    return sorted(fechas)[-1]


# ---------------------------------------------------------------------------
# Capa SILVER: aplanar y limpiar
# ---------------------------------------------------------------------------
def construir_silver_partidos(fixtures_json):
    """Aplana el JSON de fixtures a una tabla tabular limpia."""
    filas = []
    for item in fixtures_json.get("response", []):
        fx = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        league = item.get("league", {})
        status = fx.get("status", {}).get("short")
        filas.append(
            {
                "fixture_id": fx.get("id"),
                "fecha": fx.get("date"),
                "ronda": league.get("round"),
                "status": status,
                "home_id": teams.get("home", {}).get("id"),
                "home_nombre": teams.get("home", {}).get("name"),
                "away_id": teams.get("away", {}).get("id"),
                "away_nombre": teams.get("away", {}).get("name"),
                "goles_home": goals.get("home"),
                "goles_away": goals.get("away"),
            }
        )
    df = pd.DataFrame(filas)
    # Normaliza tipos y fecha
    if not df.empty:
        df["fecha"] = pd.to_datetime(df["fecha"], utc=True, errors="coerce")
        for c in ["goles_home", "goles_away"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def construir_silver_equipos(teams_json):
    """Aplana el JSON de teams a un catalogo de equipos."""
    filas = []
    for item in teams_json.get("response", []):
        t = item.get("team", {})
        v = item.get("venue", {})
        filas.append(
            {
                "equipo_id": t.get("id"),
                "nombre": t.get("name"),
                "codigo": t.get("code"),
                "fundado": t.get("founded"),
                "ciudad": v.get("city"),
                "estadio": v.get("name"),
            }
        )
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Capa GOLD: modelado analitico + Poisson
# ---------------------------------------------------------------------------
def construir_fact_partidos(df_partidos):
    """Tabla de hechos: partidos jugados (status FT) con resultado."""
    jugados = df_partidos[df_partidos["status"] == "FT"].copy()
    jugados["resultado"] = jugados.apply(
        lambda r: "H" if r["goles_home"] > r["goles_away"]
        else ("A" if r["goles_home"] < r["goles_away"] else "D"),
        axis=1,
    )
    return jugados


def calcular_fuerza_equipos(fact):
    """
    Estima fuerza ofensiva/defensiva de cada equipo (local y visitante)
    relativa al promedio de la liga. Base del modelo de Poisson (seccion 3.4).
    """
    # Promedios de liga
    prom_goles_local = fact["goles_home"].mean()
    prom_goles_visita = fact["goles_away"].mean()

    equipos = pd.unique(fact[["home_id", "away_id"]].values.ravel())
    registros = []
    for eq in equipos:
        local = fact[fact["home_id"] == eq]
        visita = fact[fact["away_id"] == eq]

        # Ataque local = goles marcados en casa / prom liga local
        atk_local = (local["goles_home"].mean() / prom_goles_local) if len(local) else 1.0
        # Defensa local = goles recibidos en casa / prom liga visita
        def_local = (local["goles_away"].mean() / prom_goles_visita) if len(local) else 1.0
        # Ataque visitante
        atk_visita = (visita["goles_away"].mean() / prom_goles_visita) if len(visita) else 1.0
        # Defensa visitante
        def_visita = (visita["goles_home"].mean() / prom_goles_local) if len(visita) else 1.0

        registros.append(
            {
                "equipo_id": eq,
                "ataque_local": round(atk_local, 4),
                "defensa_local": round(def_local, 4),
                "ataque_visitante": round(atk_visita, 4),
                "defensa_visitante": round(def_visita, 4),
                "partidos_local": len(local),
                "partidos_visitante": len(visita),
            }
        )
    df = pd.DataFrame(registros)
    df["prom_liga_local"] = round(prom_goles_local, 4)
    df["prom_liga_visita"] = round(prom_goles_visita, 4)
    return df, prom_goles_local, prom_goles_visita


def poisson_pmf(k, lam):
    """Probabilidad de exactamente k goles dado lambda (distribucion de Poisson)."""
    return (lam ** k) * exp(-lam) / factorial(k)


def probabilidades_partido(lam_home, lam_away):
    """
    Dado el numero esperado de goles de cada equipo, calcula
    P(victoria local), P(empate), P(victoria visitante) sumando
    la matriz de marcadores posibles.
    """
    p_home = p_draw = p_away = 0.0
    for gh in range(MAX_GOLES + 1):
        for ga in range(MAX_GOLES + 1):
            p = poisson_pmf(gh, lam_home) * poisson_pmf(ga, lam_away)
            if gh > ga:
                p_home += p
            elif gh == ga:
                p_draw += p
            else:
                p_away += p
    total = p_home + p_draw + p_away
    # Normaliza (por el truncamiento de la matriz)
    return p_home / total, p_draw / total, p_away / total


def construir_predicciones(df_partidos, fuerza, prom_local, prom_visita):
    """
    Para cada partido pendiente (no FT) calcula lambda esperado de cada
    equipo y las probabilidades + puntos esperados (xPts) via Poisson.
    Si no hay pendientes (temporada terminada), predice TODOS los partidos
    para poder validar contra el resultado real (seccion 6.2).
    """
    pendientes = df_partidos[df_partidos["status"] != "FT"].copy()
    modo_validacion = pendientes.empty
    objetivo = df_partidos.copy() if modo_validacion else pendientes

    f = fuerza.set_index("equipo_id")
    filas = []
    for _, p in objetivo.iterrows():
        h, a = p["home_id"], p["away_id"]
        if h not in f.index or a not in f.index:
            continue
        # lambda local = prom liga local * ataque local del home * defensa visitante del away
        lam_home = prom_local * f.loc[h, "ataque_local"] * f.loc[a, "defensa_visitante"]
        lam_away = prom_visita * f.loc[a, "ataque_visitante"] * f.loc[h, "defensa_local"]
        p_h, p_d, p_a = probabilidades_partido(lam_home, lam_away)
        filas.append(
            {
                "fixture_id": p["fixture_id"],
                "fecha": p["fecha"],
                "ronda": p["ronda"],
                "home_id": h,
                "home_nombre": p["home_nombre"],
                "away_id": a,
                "away_nombre": p["away_nombre"],
                "lambda_home": round(lam_home, 4),
                "lambda_away": round(lam_away, 4),
                "prob_victoria_local": round(p_h, 4),
                "prob_empate": round(p_d, 4),
                "prob_victoria_visitante": round(p_a, 4),
                "xpts_local": round(3 * p_h + 1 * p_d, 4),
                "xpts_visitante": round(3 * p_a + 1 * p_d, 4),
                "modo": "validacion" if modo_validacion else "prediccion",
            }
        )
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Escritura en S3 (Parquet via awswrangler, registra en Glue Catalog)
# ---------------------------------------------------------------------------
def escribir_parquet(df, capa, tabla, database=None, crear_tabla=False):
    """Escribe un DataFrame a Parquet en S3. Opcionalmente cataloga en Glue."""
    path = f"s3://{S3_BUCKET}/{capa}/{tabla}/"
    if crear_tabla and database:
        wr.s3.to_parquet(
            df=df, path=path, dataset=True, mode="overwrite",
            database=database, table=tabla,
        )
    else:
        wr.s3.to_parquet(df=df, path=path, dataset=True, mode="overwrite")
    logger.info("Escrito %s (%d filas) en %s", tabla, len(df), path)
    return path


def lambda_handler(event, context):
    """Orquesta la transformacion completa bronze -> silver -> gold."""
    database = os.environ.get("GLUE_DATABASE", "brasileirao_db")
    catalogar = os.environ.get("CATALOGAR", "true").lower() == "true"

    # 1. Localizar particiones mas recientes y leer bronze
    fecha_fx = fecha_mas_reciente("fixtures")
    fecha_tm = fecha_mas_reciente("teams")
    fixtures_json = leer_json_bronze("fixtures", fecha_fx)
    teams_json = leer_json_bronze("teams", fecha_tm)

    # 2. SILVER
    df_partidos = construir_silver_partidos(fixtures_json)
    df_equipos = construir_silver_equipos(teams_json)
    escribir_parquet(df_partidos, "silver", "partidos")
    escribir_parquet(df_equipos, "silver", "equipos")

    # 3. GOLD
    fact = construir_fact_partidos(df_partidos)
    fuerza, prom_l, prom_v = calcular_fuerza_equipos(fact)
    predicciones = construir_predicciones(df_partidos, fuerza, prom_l, prom_v)

    escribir_parquet(fact, "gold", "fact_partidos", database, catalogar)
    escribir_parquet(df_equipos, "gold", "dim_equipos", database, catalogar)
    escribir_parquet(fuerza, "gold", "fuerza_equipos", database, catalogar)
    escribir_parquet(predicciones, "gold", "predicciones", database, catalogar)

    resumen = {
        "fecha_datos": fecha_fx,
        "season": SEASON,
        "silver_partidos": len(df_partidos),
        "silver_equipos": len(df_equipos),
        "gold_fact_partidos": len(fact),
        "gold_fuerza_equipos": len(fuerza),
        "gold_predicciones": len(predicciones),
    }
    logger.info("Transformacion finalizada: %s", json.dumps(resumen))
    return {"statusCode": 200, "body": json.dumps(resumen)}


if __name__ == "__main__":
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for linea in fh:
                linea = linea.strip()
                if linea and not linea.startswith("#") and "=" in linea:
                    k, v = linea.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    print(json.dumps(lambda_handler({}, None), indent=2, ensure_ascii=False))
