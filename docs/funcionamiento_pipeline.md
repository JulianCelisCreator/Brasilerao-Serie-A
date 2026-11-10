# Funcionamiento del pipeline y las capas de S3

Documento técnico que explica cómo opera el pipeline del Brasileirão hoy, qué
hace cada componente y qué contiene cada capa del *data lake* en S3.

> Estado verificado contra la cuenta AWS `851725543887`, región `us-east-2`,
> bucket `brasileirao-datalake-851725543887`.

---

## 1. Visión general del flujo

```
  API-Football
       │  (HTTPS, x-apisports-key desde SSM Parameter Store)
       ▼
  Lambda  brasileirao-extraccion      ──►  S3  bronze/   (JSON crudo)
       │
       ▼
  Lambda  brasileirao-transformacion  ──►  S3  silver/   (Parquet limpio)
       │                                └─►  S3  gold/     (Parquet analítico)
       │                                       │
       │                                  Glue Data Catalog (brasileirao_db)
       ▼                                       │
  Amazon Athena (workgroup brasileirao-wg) ◄───┘
       │   resultados de cada consulta
       ▼
  S3  athena-results/   ──►  Power BI (conector ODBC, modo Importar)
```

Arquitectura **medallón** (bronze → silver → gold) y **serverless**: nada corre
de forma continua, cada pieza se ejecuta solo cuando se invoca.

---

## 2. Componentes de cómputo (Lambda)

### 2.1 `brasileirao-extraccion`

| Propiedad | Valor |
|-----------|-------|
| Runtime   | `python3.12` |
| Handler   | `lambda_function.lambda_handler` |
| Timeout   | 60 s |
| Memoria   | 256 MB |
| Código    | `lambda-extraccion/lambda_function.py` |

Qué hace:
1. Lee la API key desde **SSM Parameter Store** (`/brasileirao/api-key`, cifrado).
   En local puede leerla de un `.env`.
2. Consulta tres *endpoints* de API-Football para la liga `71` (Brasileirão
   Serie A), temporada `2024`: `standings`, `teams`, `fixtures`.
3. Guarda cada respuesta **sin modificar** en la capa bronze.

Variables de entorno configuradas:
`S3_BUCKET`, `SSM_PARAM_NAME`, `LEAGUE_ID=71`, `API_FOOTBALL_HOST`, `SEASON=2024`.

### 2.2 `brasileirao-transformacion`

| Propiedad | Valor |
|-----------|-------|
| Runtime   | `python3.12` |
| Handler   | `lambda_function.lambda_handler` |
| Timeout   | 120 s |
| Memoria   | 512 MB |
| Layer     | `AWSSDKPandas-Python312:32` (incluye pandas, numpy, pyarrow, awswrangler) |
| Código    | `lambda-transformacion/lambda_function.py` |

Qué hace:
1. Localiza la partición de fecha más reciente en `bronze/fixtures/` y
   `bronze/teams/` y lee esos JSON.
2. **SILVER:** aplana el JSON anidado a tablas (`partidos`, `equipos`),
   normaliza tipos y fechas, y escribe Parquet en `silver/`.
3. **GOLD:** construye el modelo analítico y escribe Parquet en `gold/`,
   catalogando cada tabla en Glue (`GLUE_DATABASE=brasileirao_db`,
   `CATALOGAR=true`):
   - `fact_partidos` — partidos jugados (status `FT`) con resultado H/D/A.
   - `dim_equipos` — catálogo de equipos.
   - `fuerza_equipos` — fuerza ofensiva/defensiva (local y visitante) relativa
     al promedio de la liga.
   - `predicciones` — modelo de **Poisson**: para cada partido calcula el número
     esperado de goles (λ) de cada equipo, las probabilidades de
     victoria/empate/derrota y los puntos esperados (xPts). Si no hay partidos
     pendientes (temporada terminada), predice todos los partidos en modo
     "validación" para comparar contra el resultado real.

Variables de entorno: `S3_BUCKET`, `CATALOGAR=true`, `GLUE_DATABASE=brasileirao_db`,
`MAX_GOLES=10` (tope de goles para la matriz de Poisson), `SEASON=2024`.

---

## 3. Las cuatro carpetas de S3

Todas viven en el mismo bucket `brasileirao-datalake-851725543887`.

### 3.1 `bronze/` — datos crudos

JSON tal como lo devuelve la API, particionado por entidad y fecha. Conservar el
crudo permite reprocesar sin volver a consumir la API.

Ruta: `bronze/<entidad>/fecha=AAAA-MM-DD/<entidad>.json`

Contenido actual:

```
bronze/fixtures/fecha=2026-11-07/fixtures.json     (~409 KB)
bronze/standings/fecha=2026-11-07/standings.json   (~11 KB)
bronze/teams/fecha=2026-11-07/teams.json           (~8 KB)
```

> **Nota:** `standings` se extrae y se guarda en bronze, pero la Lambda de
> transformación **no lo consume** actualmente (solo usa `fixtures` y `teams`).
> Es un dato disponible para uso futuro, no una tabla gold.

### 3.2 `silver/` — datos limpios y tabulares

Parquet con compresión Snappy, resultado de aplanar y normalizar el JSON.

```
silver/equipos/   *.snappy.parquet    (~5 KB)
silver/partidos/  *.snappy.parquet    (~12 KB)
```

- `partidos`: una fila por partido con `fixture_id`, `fecha`, `ronda`, `status`,
  ids y nombres de local/visitante, y goles.
- `equipos`: catálogo con id, nombre, código, fundación, ciudad, estadio.

### 3.3 `gold/` — modelo analítico (lo que consume Power BI)

Parquet Snappy. Cada carpeta está catalogada como tabla en Glue
`brasileirao_db`, por lo que es consultable con SQL desde Athena.

```
gold/dim_equipos/      *.snappy.parquet   (~5 KB)
gold/fact_partidos/    *.snappy.parquet   (~12 KB)
gold/fuerza_equipos/   *.snappy.parquet   (~6 KB)
gold/predicciones/     *.snappy.parquet   (~33 KB)
```

Esquema de cada tabla (según Glue Data Catalog):

**`fact_partidos`** — `fixture_id` (bigint), `fecha` (timestamp), `ronda`,
`status`, `home_id`, `home_nombre`, `away_id`, `away_nombre`, `goles_home`,
`goles_away`, `resultado` (H/D/A).

**`dim_equipos`** — `equipo_id` (bigint), `nombre`, `codigo`, `fundado`,
`ciudad`, `estadio`.

**`fuerza_equipos`** — `equipo_id`, `ataque_local`, `defensa_local`,
`ataque_visitante`, `defensa_visitante` (double), `partidos_local`,
`partidos_visitante`, `prom_liga_local`, `prom_liga_visita`.

**`predicciones`** — `fixture_id`, `fecha`, `ronda`, ids y nombres de equipos,
`lambda_home`, `lambda_away`, `prob_victoria_local`, `prob_empate`,
`prob_victoria_visitante`, `xpts_local`, `xpts_visitante`, `modo`
(prediccion / validacion).

### 3.4 `athena-results/` — resultados de consultas

Athena deposita aquí el resultado de **cada consulta** que se ejecuta desde el
workgroup `brasileirao-wg`. No es una capa de datos del pipeline: es el "buzón
de salida" que Athena (y por tanto Power BI) necesita para funcionar.

```
athena-results/<query-id>.csv            ← filas devueltas por la consulta
athena-results/<query-id>.csv.metadata   ← metadatos (tipos de columna, etc.)
```

Se genera un par de archivos por cada query. Conviene aplicar una regla de ciclo
de vida en S3 para borrar estos resultados pasados unos días y no acumular
basura (no afectan a las tablas).

---

## 4. Catálogo y consulta (Glue + Athena)

- **Glue Data Catalog** (`brasileirao_db`) guarda el esquema de las 4 tablas gold.
  La Lambda de transformación registra/actualiza estas tablas al escribir
  (`CATALOGAR=true`), así que no se necesita un Crawler.
- **Athena** ejecuta SQL estándar sobre esas tablas leyendo el Parquet de `gold/`.
  Workgroup `brasileirao-wg`, con resultados en `s3://.../athena-results/`.

Ejemplo verificado (estado `SUCCEEDED`):

```sql
SELECT home_nombre, away_nombre,
       prob_victoria_local, prob_empate, prob_victoria_visitante
FROM brasileirao_db.predicciones
LIMIT 5;
```

Devolvió, entre otros: `Internacional vs Bahia → 0.6149 / 0.2104 / 0.1747`
(las tres probabilidades suman ~1.0, lo que confirma que el modelo Poisson está
normalizado correctamente).

---

## 5. Orquestación: estado actual

Aquí hay una diferencia entre el diseño de la propuesta y lo desplegado hoy:

| Pieza | Estado real |
|-------|-------------|
| EventBridge Scheduler `brasileirao-extraccion-diaria` | **DISABLED**. Programado como `cron(0 9 * * ? *)` en `America/Sao_Paulo` (diario 9:00), apuntando a la Lambda de extracción. Está creado pero apagado. |
| Trigger extracción → transformación | **No existe.** No hay notificación de evento S3 ni regla de EventBridge que dispare la transformación tras la extracción. |
| EventBridge Rules | Ninguna. |

**Conclusión:** hoy el pipeline **no es automático de punta a punta**. Para
procesar datos nuevos se invocan las dos Lambdas manualmente (o se habilita el
scheduler para la extracción y se dispara la transformación aparte).

Para automatizarlo por completo, las opciones serían:
1. Habilitar el scheduler (`aws scheduler update-schedule ... --state ENABLED`)
   para la extracción diaria, y
2. Encadenar la transformación mediante una notificación de evento S3 sobre el
   prefijo `bronze/` o un segundo schedule desfasado unos minutos.

---

## 6. Seguridad y costos (resumen)

- **Secretos:** la API key se lee de SSM Parameter Store, no del código.
  (Pendiente: hay una API key *hardcodeada* en
  `lambda-transformacion/test_local.py` línea 12 que conviene mover al `.env`.)
- **IAM:** roles de mínimo privilegio por servicio en `aws-infra/iam/`.
- **Costos:** Lambda y el catálogo caen en el *free tier*; S3 guarda unos pocos
  cientos de KB; Athena cobra por datos escaneados (KB → ~0 USD). Presupuesto
  `brasileirao-budget` de 5 USD/mes con alertas al 50/80/100 %.

---

## 7. Cómo ejecutar el pipeline manualmente

```powershell
# 1. Extracción: API -> bronze
aws lambda invoke --function-name brasileirao-extraccion `
  --region us-east-2 salida-extraccion.json

# 2. Transformación: bronze -> silver -> gold (+ catálogo Glue)
aws lambda invoke --function-name brasileirao-transformacion `
  --region us-east-2 salida-transformacion.json
```

Tras el paso 2, las tablas gold quedan actualizadas y disponibles para Athena y
Power BI (ver `docs/conexion_powerbi_athena.md`).
