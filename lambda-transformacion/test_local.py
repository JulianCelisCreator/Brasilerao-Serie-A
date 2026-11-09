"""Prueba local de la logica de transformacion y Poisson (sin tocar S3)."""
import json
import urllib.request

import lambda_function as lf

KEY = "ebf6fa9b8e7d4f69fe073b0697d8fe20"
HOST = "v3.football.api-sports.io"


def get(endpoint):
    req = urllib.request.Request(f"https://{HOST}/{endpoint}")
    req.add_header("x-apisports-key", KEY)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


print("Descargando datos reales 2024...")
fixtures = get("fixtures?league=71&season=2024")
teams = get("teams?league=71&season=2024")

# SILVER
df_part = lf.construir_silver_partidos(fixtures)
df_eq = lf.construir_silver_equipos(teams)
print(f"\nSILVER partidos: {len(df_part)} filas | equipos: {len(df_eq)} filas")
print(f"Status unicos: {df_part['status'].value_counts().to_dict()}")

# GOLD
fact = lf.construir_fact_partidos(df_part)
print(f"\nGOLD fact_partidos (jugados FT): {len(fact)}")
print(f"Distribucion resultados: {fact['resultado'].value_counts().to_dict()}")

fuerza, pl, pv = lf.calcular_fuerza_equipos(fact)
print(f"\nProm goles liga local={pl:.3f} visita={pv:.3f}")
print("Top 3 ataque local:")
print(fuerza.nlargest(3, "ataque_local")[["equipo_id", "ataque_local", "defensa_local"]].to_string(index=False))

pred = lf.construir_predicciones(df_part, fuerza, pl, pv)
print(f"\nGOLD predicciones: {len(pred)} filas (modo: {pred['modo'].iloc[0] if len(pred) else 'N/A'})")

# Validacion: probabilidades deben sumar ~1
pred["suma_prob"] = (pred["prob_victoria_local"] + pred["prob_empate"]
                     + pred["prob_victoria_visitante"])
print(f"Suma de probabilidades (debe ser ~1.0): min={pred['suma_prob'].min():.4f} "
      f"max={pred['suma_prob'].max():.4f}")

print("\nEjemplo de 3 predicciones:")
cols = ["home_nombre", "away_nombre", "prob_victoria_local", "prob_empate",
        "prob_victoria_visitante", "xpts_local", "xpts_visitante"]
print(pred[cols].head(3).to_string(index=False))
