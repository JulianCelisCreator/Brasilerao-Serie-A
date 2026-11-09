"""
Lambda de extraccion - Pipeline Brasileirao (capa bronze).

Consulta la API-Football (api-sports.io) y deposita el JSON crudo en S3
siguiendo la arquitectura medallon:
    s3://<bucket>/bronze/<entidad>/fecha=AAAA-MM-DD/<entidad>.json

La API key se lee desde AWS Systems Manager Parameter Store (SecureString),
nunca desde el codigo. En ejecucion local se puede usar un archivo .env.

Entidades extraidas (liga 71 = Brasileirao Serie A):
    - standings : tabla de posiciones actual
    - teams     : equipos de la temporada
    - fixtures  : todos los partidos (jugados y pendientes)

Autor: pipeline brasileirao
"""

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ----------------------------------------------------------------------------
# Configuracion por variables de entorno (con defaults del proyecto)
# ----------------------------------------------------------------------------
API_HOST = os.environ.get("API_FOOTBALL_HOST", "v3.football.api-sports.io")
S3_BUCKET = os.environ.get("S3_BUCKET", "brasileirao-datalake-851725543887")
SSM_PARAM_NAME = os.environ.get("SSM_PARAM_NAME", "/brasileirao/api-key")
LEAGUE_ID = os.environ.get("LEAGUE_ID", "71")
# Plan Free de API-Football solo permite temporadas 2022-2024. Usamos 2024 (completa).
SEASON = os.environ.get("SEASON", "2024")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")

# Entidades a extraer: nombre logico -> (endpoint, params adicionales)
ENTIDADES = {
    "standings": ("standings", {}),
    "teams": ("teams", {}),
    "fixtures": ("fixtures", {}),
}


def obtener_api_key():
    """Lee la API key desde Parameter Store (AWS) o variable de entorno (local)."""
    # En local, si existe la variable, se usa directo (para pruebas).
    key_local = os.environ.get("API_FOOTBALL_KEY")
    if key_local and key_local != "tu_api_key_aqui":
        logger.info("Usando API key desde variable de entorno (modo local).")
        return key_local

    logger.info("Leyendo API key desde Parameter Store: %s", SSM_PARAM_NAME)
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    resp = ssm.get_parameter(Name=SSM_PARAM_NAME, WithDecryption=True)
    return resp["Parameter"]["Value"]


def consultar_api(endpoint, params, api_key):
    """Hace una peticion GET a la API-Football y devuelve el JSON parseado."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://{API_HOST}/{endpoint}"
    if query:
        url = f"{url}?{query}"

    req = urllib.request.Request(url)
    req.add_header("x-apisports-key", api_key)
    req.add_header("x-rapidapi-host", API_HOST)

    logger.info("GET %s", url)
    with urllib.request.urlopen(req, timeout=25) as r:
        datos = json.loads(r.read().decode("utf-8"))

    # La API devuelve errores dentro del cuerpo, no como HTTP != 200
    if datos.get("errors"):
        logger.warning("La API reporto errores para %s: %s", endpoint, datos["errors"])
    return datos


def guardar_en_bronze(s3, entidad, contenido, fecha):
    """Guarda el JSON crudo en la ruta medallon de la capa bronze."""
    key = f"bronze/{entidad}/fecha={fecha}/{entidad}.json"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(contenido, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Guardado s3://%s/%s", S3_BUCKET, key)
    return key


def lambda_handler(event, context):
    """Punto de entrada. Extrae todas las entidades y las deposita en bronze."""
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    api_key = obtener_api_key()
    s3 = boto3.client("s3", region_name=AWS_REGION)

    resultados = {}
    base_params = {"league": LEAGUE_ID, "season": SEASON}

    for entidad, (endpoint, extra) in ENTIDADES.items():
        try:
            params = {**base_params, **extra}
            datos = consultar_api(endpoint, params, api_key)
            key = guardar_en_bronze(s3, entidad, datos, fecha)
            resultados[entidad] = {
                "ok": True,
                "s3_key": key,
                "results": datos.get("results"),
            }
        except urllib.error.HTTPError as e:
            logger.error("HTTPError en %s: %s", entidad, e)
            resultados[entidad] = {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            logger.error("Error en %s: %s", entidad, e)
            resultados[entidad] = {"ok": False, "error": str(e)}

    cuerpo = {
        "fecha": fecha,
        "league": LEAGUE_ID,
        "season": SEASON,
        "resultados": resultados,
    }
    logger.info("Extraccion finalizada: %s", json.dumps(cuerpo))
    return {"statusCode": 200, "body": json.dumps(cuerpo)}


# Permite ejecutar localmente: python lambda_function.py
if __name__ == "__main__":
    # Carga simple del .env para pruebas locales (sin dependencias externas)
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for linea in fh:
                linea = linea.strip()
                if linea and not linea.startswith("#") and "=" in linea:
                    k, v = linea.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    print(json.dumps(lambda_handler({}, None), indent=2, ensure_ascii=False))
