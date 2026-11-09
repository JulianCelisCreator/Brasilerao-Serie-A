# Conexión de Power BI a Amazon Athena

Guía para importar las tablas de la capa **gold** del pipeline del Brasileirão
hacia Power BI Desktop usando el conector nativo de Amazon Athena.

> **Resumen:** toda la infraestructura de AWS ya está creada y verificada.
> Estos pasos son del lado de tu PC (Windows + Power BI Desktop).

---

## Datos de conexión del proyecto

Ten a mano estos valores; se usan al configurar el DSN:

| Parámetro               | Valor                                                         |
|-------------------------|---------------------------------------------------------------|
| Región AWS              | `us-east-2`                                                   |
| Catálogo                | `AwsDataCatalog`                                              |
| Base de datos (Glue)    | `brasileirao_db`                                             |
| Workgroup de Athena     | `brasileirao-wg`                                             |
| Carpeta de resultados S3| `s3://brasileirao-datalake-851725543887/athena-results/`     |
| Cuenta AWS              | `851725543887`                                               |

Tablas disponibles en `brasileirao_db`:

| Tabla             | Contenido                                                       |
|-------------------|-----------------------------------------------------------------|
| `fact_partidos`   | Un registro por partido jugado (goles, local/visitante, resultado) |
| `dim_equipos`     | Catálogo de equipos                                             |
| `fuerza_equipos`  | Fuerza ofensiva/defensiva de cada equipo (local y visitante)    |
| `predicciones`    | P(victoria/empate/derrota) y puntos esperados (modelo Poisson)  |

---

## Fase 1 — Instalar el driver ODBC de Athena

El conector nativo "Amazon Athena" de Power BI **requiere** el driver ODBC de
Athena instalado en Windows. Sin él, la conexión no funciona.

1. Descarga el **Amazon Athena ODBC driver 2.x** (64 bits, Windows) desde la
   página oficial de AWS:
   <https://docs.aws.amazon.com/athena/latest/ug/connect-with-odbc.html>
2. Ejecuta el instalador `.msi` (doble clic → Siguiente → Finalizar).

---

## Fase 2 — Crear un DSN (origen de datos ODBC)

El conector de Power BI pide un **DSN**: una conexión ODBC con nombre, guardada
en Windows.

1. Abre **"ODBC Data Sources (64-bit)"** desde el menú Inicio.
2. Pestaña **System DSN** → **Add** → selecciona **Amazon Athena ODBC**.
3. Rellena los campos:

   - **Data Source Name (DSN):** `brasileirao-athena`
   - **AWS Region:** `us-east-2`
   - **Catalog:** `AwsDataCatalog`
   - **Database:** `brasileirao_db`
   - **Workgroup:** `brasileirao-wg`
   - **S3 Output Location:** `s3://brasileirao-datalake-851725543887/athena-results/`
   - **Authentication Type:** `IAM Credentials`
     → ingresa tu **Access Key ID** y **Secret Access Key**.

   > Aquí sí se ingresan las llaves: es un diálogo local de Windows que las
   > guarda cifradas en tu equipo, no es lo mismo que exponerlas en texto.
   > Usa las mismas credenciales que configuraste con `aws configure`.

4. Pulsa **Test** para comprobar la conexión y luego **OK**.

   - Si **Test** falla con un error de permisos (`AccessDenied`,
     `not authorized to perform athena:...`), el usuario IAM necesita una
     política de Athena. Ver la sección **Permisos IAM necesarios** más abajo.

---

## Fase 3 — Conectar desde Power BI Desktop

1. Abre **Power BI Desktop** → pestaña **Inicio** → **Obtener datos**.
2. En el buscador escribe **Athena** → selecciona **Amazon Athena** → **Conectar**.
3. En **DSN** escribe `brasileirao-athena`.
4. En **Modo de conectividad de datos** elige:
   - **Importar** (recomendado para este proyecto): los datos son pequeños y se
     cargan completos en Power BI. Permite trabajar sin conexión y es más rápido.
   - **DirectQuery**: Power BI consulta Athena en vivo cada vez (no importa datos).
     Útil para volúmenes grandes; no es necesario aquí.
5. Pulsa **Aceptar** → elige **Usar configuración del origen de datos** → **Conectar**.
6. En el **Navegador** se despliega `AwsDataCatalog` → `brasileirao_db` con las 4
   tablas. Marca las que quieras usar.
7. (Opcional) **Transformar datos** abre el Editor de Power Query para filtrar o
   ajustar antes de cargar.
8. Pulsa **Cargar**.

Los datos quedan importados y listos para construir el informe.

---

## Ideas para el informe en Power BI

- **Tabla actual vs. proyectada:** puntos reales (de `fact_partidos`) frente a
  puntos esperados acumulados (de `predicciones`, campo `xpts_*`).
- **Probabilidades por partido:** barras apiladas con
  `prob_victoria_local` / `prob_empate` / `prob_victoria_visitante`.
- **Ranking de fuerza:** `fuerza_equipos` ordenado por ataque/defensa.
- **Evolución de puntos:** línea por jornada (`ronda`).

---

## Permisos IAM necesarios (si el Test del DSN falla)

El usuario IAM que uses en el DSN necesita permisos para: ejecutar consultas en
Athena, leer el catálogo de Glue y leer/escribir en S3 (los datos gold y la
carpeta de resultados). Política de mínimo privilegio sugerida:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AthenaConsultas",
      "Effect": "Allow",
      "Action": [
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetQueryResults",
        "athena:StopQueryExecution",
        "athena:GetWorkGroup"
      ],
      "Resource": "arn:aws:athena:us-east-2:851725543887:workgroup/brasileirao-wg"
    },
    {
      "Sid": "GlueCatalogoLectura",
      "Effect": "Allow",
      "Action": [
        "glue:GetDatabase",
        "glue:GetDatabases",
        "glue:GetTable",
        "glue:GetTables",
        "glue:GetPartition",
        "glue:GetPartitions"
      ],
      "Resource": [
        "arn:aws:glue:us-east-2:851725543887:catalog",
        "arn:aws:glue:us-east-2:851725543887:database/brasileirao_db",
        "arn:aws:glue:us-east-2:851725543887:table/brasileirao_db/*"
      ]
    },
    {
      "Sid": "S3LeerDatosGold",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::brasileirao-datalake-851725543887",
        "arn:aws:s3:::brasileirao-datalake-851725543887/gold/*"
      ]
    },
    {
      "Sid": "S3ResultadosAthena",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::brasileirao-datalake-851725543887",
        "arn:aws:s3:::brasileirao-datalake-851725543887/athena-results/*"
      ]
    }
  ]
}
```

Para aplicarla (requiere permisos de administrador IAM), guárdala como
`aws-infra/iam/athena-powerbi-permissions-policy.json` y ejecuta:

```powershell
aws iam put-user-policy `
  --user-name kiro-mcp-user `
  --policy-name AthenaPowerBIReadOnly `
  --policy-document file://aws-infra/iam/athena-powerbi-permissions-policy.json
```

---

## Notas de costos (FinOps)

- Athena cobra **por datos escaneados** (~5 USD por TB). Las tablas gold pesan
  unos pocos KB en Parquet, así que el costo real es prácticamente **0 USD**.
- El modo **Importar** de Power BI ejecuta la consulta solo al refrescar, no de
  forma continua. Refresca manualmente o de forma programada tras cada jornada.
- El formato Parquet + particiones minimiza los datos escaneados.
- Comparado con Amazon Redshift, Athena evita el costo base por RPU-hora, que es
  el mayor riesgo frente al presupuesto de 5 USD/mes del proyecto.

---

## Verificación realizada

El camino completo fue probado con el AWS CLI antes de redactar esta guía:

- Workgroup `brasileirao-wg` activo, con carpeta de resultados configurada.
- Consulta `SELECT ... FROM brasileirao_db.predicciones LIMIT 5` → estado
  `SUCCEEDED`, devolviendo probabilidades que suman ~1.0 por partido.
- Glue Database `brasileirao_db` con las 4 tablas gold catalogadas.
