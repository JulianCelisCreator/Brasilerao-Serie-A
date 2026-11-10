# Tablas de Athena y la carpeta `athena-results/`

Este documento explica las tablas que se consultan a través de Amazon Athena
(la base de datos `brasileirao_db` del Glue Data Catalog) y aclara qué es
realmente la carpeta `athena-results/` de S3.

---

## 1. Las tablas consultables (`brasileirao_db`)

Son las 4 tablas de la capa **gold**, registradas en el Glue Data Catalog. Estas
son las que Power BI importa vía el conector ODBC de Athena.

### 1.1 `fact_partidos`

Tabla de hechos: un registro por **partido ya jugado** (status `FT`).

| Columna       | Tipo      | Descripción |
|---------------|-----------|-------------|
| `fixture_id`  | bigint    | ID único del partido (API-Football). Llave para unir con `predicciones`. |
| `fecha`       | timestamp | Fecha y hora del partido (UTC). |
| `ronda`       | string    | Jornada, ej. `"Regular Season - 15"`. |
| `status`      | string    | Estado del partido; aquí siempre `FT` (finalizado). |
| `home_id`     | bigint    | ID del equipo local → `dim_equipos.equipo_id`. |
| `home_nombre` | string    | Nombre del equipo local. |
| `away_id`     | bigint    | ID del equipo visitante → `dim_equipos.equipo_id`. |
| `away_nombre` | string    | Nombre del equipo visitante. |
| `goles_home`  | bigint    | Goles del local. |
| `goles_away`  | bigint    | Goles del visitante. |
| `resultado`   | string    | `H` (gana local), `D` (empate), `A` (gana visitante). |

### 1.2 `dim_equipos`

Dimensión: catálogo de equipos de la temporada.

| Columna     | Tipo   | Descripción |
|-------------|--------|-------------|
| `equipo_id` | bigint | ID único del equipo (llave de dimensión). |
| `nombre`    | string | Nombre del equipo. |
| `codigo`    | string | Código corto, ej. `"INT"`. |
| `fundado`   | bigint | Año de fundación. |
| `ciudad`    | string | Ciudad del estadio. |
| `estadio`   | string | Nombre del estadio. |

### 1.3 `fuerza_equipos`

Indicadores de fuerza ofensiva y defensiva de cada equipo, relativos al promedio
de la liga. Es la base matemática del modelo de Poisson.

| Columna             | Tipo   | Descripción |
|---------------------|--------|-------------|
| `equipo_id`         | bigint | ID del equipo → `dim_equipos.equipo_id`. |
| `ataque_local`      | double | Goles marcados de local ÷ promedio de local de la liga. **>1 = mejor ataque.** |
| `defensa_local`     | double | Goles recibidos de local ÷ promedio de visita de la liga. **<1 = mejor defensa.** |
| `ataque_visitante`  | double | Goles marcados de visitante ÷ promedio de visita de la liga. **>1 = mejor ataque.** |
| `defensa_visitante` | double | Goles recibidos de visitante ÷ promedio de local de la liga. **<1 = mejor defensa.** |
| `partidos_local`    | bigint | Nº de partidos jugados como local (respaldo de la muestra). |
| `partidos_visitante`| bigint | Nº de partidos jugados como visitante. |
| `prom_liga_local`   | double | Promedio de goles del local en toda la liga (constante). |
| `prom_liga_visita`  | double | Promedio de goles del visitante en toda la liga (constante). |

> Los valores están centrados en **1.0** = promedio de la liga. En ataque, más
> alto es mejor; en defensa, más bajo es mejor (menos goles recibidos).
>
> **Origen de los promedios de liga:** se calculan sobre `fact_partidos`
> (solo partidos jugados) como el promedio de la columna de goles:
> `prom_liga_local = media(goles_home)` y `prom_liga_visita = media(goles_away)`.
> Son un único número por liga, por eso se repiten igual en todas las filas.

### 1.4 `predicciones`

Salida del modelo de Poisson: probabilidades y puntos esperados por partido.

| Columna                   | Tipo      | Descripción |
|---------------------------|-----------|-------------|
| `fixture_id`              | bigint    | ID del partido → une con `fact_partidos`. |
| `fecha`                   | timestamp | Fecha y hora del partido (UTC). |
| `ronda`                   | string    | Jornada. |
| `home_id` / `away_id`     | bigint    | IDs de los equipos. |
| `home_nombre` / `away_nombre` | string | Nombres de los equipos. |
| `lambda_home`             | double    | Goles esperados del local (λ). |
| `lambda_away`             | double    | Goles esperados del visitante (λ). |
| `prob_victoria_local`     | double    | P(gana local), 0 a 1. |
| `prob_empate`             | double    | P(empate). |
| `prob_victoria_visitante` | double    | P(gana visitante). Las tres suman ~1.0. |
| `xpts_local`              | double    | Puntos esperados del local = `3·P(local) + 1·P(empate)`. |
| `xpts_visitante`          | double    | Puntos esperados del visitante = `3·P(visitante) + 1·P(empate)`. |
| `modo`                    | string    | `prediccion` (partidos pendientes) o `validacion` (temporada terminada, se predicen todos). |

**Cálculo de los λ** (usa `fuerza_equipos`):

```
lambda_home = prom_liga_local  × ataque_local(home)     × defensa_visitante(away)
lambda_away = prom_liga_visita × ataque_visitante(away) × defensa_local(home)
```

Las probabilidades se obtienen recorriendo todos los marcadores posibles
(0-0 hasta 10-10, por `MAX_GOLES=10`) con la fórmula de Poisson
`P(k) = λ^k · e^(−λ) / k!` y sumándolos según quién gana; al final se normalizan
para que sumen 1.

---

## 2. Relaciones entre tablas (para Power BI)

```
dim_equipos (equipo_id)
     ▲                 ▲
     │ home_id/away_id │ equipo_id
     │                 │
fact_partidos      fuerza_equipos
     ▲
     │ fixture_id
     │
 predicciones
```

- `fact_partidos.home_id` / `away_id` → `dim_equipos.equipo_id`
- `predicciones.home_id` / `away_id` → `dim_equipos.equipo_id`
- `fuerza_equipos.equipo_id` → `dim_equipos.equipo_id`
- `predicciones.fixture_id` → `fact_partidos.fixture_id` (para validar
  predicción contra resultado real)

---

## 3. Qué es realmente `athena-results/`

`athena-results/` es la **ubicación de resultados** del workgroup
`brasileirao-wg`. No es una capa de datos ni contiene tablas de negocio: es el
lugar donde Athena guarda, de forma automática, la salida de **cada consulta SQL**
que se ejecuta (desde la consola, el AWS CLI o Power BI).

Por cada consulta se crean dos archivos:

| Archivo                     | Contenido |
|-----------------------------|-----------|
| `<query-id>.csv`            | Las filas que devolvió esa consulta puntual (formato CSV). |
| `<query-id>.csv.metadata`   | Metadatos de la consulta: tipos de columna, estadísticas de ejecución, etc. |

Características:

- El `<query-id>` es un identificador único (UUID) de cada ejecución, ej.
  `b92bc2d4-53af-494e-9287-4977d3bcee7c.csv`.
- **No tienen esquema fijo**: las columnas dependen del `SELECT` que se corrió.
- Son **desechables**. Borrarlos no afecta a las tablas ni al pipeline; Athena
  los regenera en la siguiente consulta.
- Power BI, al conectarse en modo Importar, ejecuta consultas que también dejan
  su CSV aquí.

**Recomendación (FinOps):** aplicar una regla de ciclo de vida en S3 que elimine
los objetos de `athena-results/` pasados unos días, para no acumular archivos
temporales. No afecta los datos del proyecto.

---

## 4. Consultas SQL de ejemplo (Athena)

Ranking de ataque local:

```sql
SELECT e.nombre, f.ataque_local, f.partidos_local
FROM brasileirao_db.fuerza_equipos f
JOIN brasileirao_db.dim_equipos e ON e.equipo_id = f.equipo_id
ORDER BY f.ataque_local DESC;
```

Mejores defensas de local (recuerda: más bajo = mejor):

```sql
SELECT e.nombre, f.defensa_local
FROM brasileirao_db.fuerza_equipos f
JOIN brasileirao_db.dim_equipos e ON e.equipo_id = f.equipo_id
ORDER BY f.defensa_local ASC;
```

Validar el modelo (predicción vs. resultado real):

```sql
SELECT p.home_nombre, p.away_nombre,
       p.prob_victoria_local, p.prob_empate, p.prob_victoria_visitante,
       f.resultado
FROM brasileirao_db.predicciones p
JOIN brasileirao_db.fact_partidos f ON f.fixture_id = p.fixture_id;
```

Tabla proyectada (puntos esperados sumando local + visitante):

```sql
SELECT e.nombre,
       SUM(CASE WHEN p.home_id = e.equipo_id THEN p.xpts_local ELSE 0 END)
     + SUM(CASE WHEN p.away_id = e.equipo_id THEN p.xpts_visitante ELSE 0 END)
       AS puntos_esperados
FROM brasileirao_db.predicciones p
JOIN brasileirao_db.dim_equipos e
     ON e.equipo_id IN (p.home_id, p.away_id)
GROUP BY e.nombre
ORDER BY puntos_esperados DESC;
```
