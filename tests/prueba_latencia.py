import requests
import time
import statistics

BASE_URL = "https://web-production-320c0.up.railway.app"

payload_quote = {
    "lat": 5.62,
    "lon": -75.45,
    "hectareas": 3.5,
    "suma_asegurada_usd_ha": 300.0,
    "cobertura": 0.80,
    "loading": 0.20
}

payload_event = {
    "lat": 5.62,
    "lon": -75.45,
    "fecha_evento": "2026-04-06",
    "hectareas": 3.5,
    "suma_asegurada_usd_ha": 300.0,
    "cobertura": 0.80,
    "loading": 0.20
}

def medir_latencia(endpoint, payload, n=100):
    url     = f"{BASE_URL}{endpoint}"
    tiempos = []
    errores = 0

    print(f"\nProbando {endpoint} — {n} consultas...")
    for i in range(n):
        t0 = time.time()
        try:
            r  = requests.post(url, json=payload, timeout=10)
            t1 = time.time()
            ms = round((t1 - t0) * 1000, 1)
            tiempos.append(ms)
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{n} completadas — última: {ms} ms "
                      f"(HTTP {r.status_code})")
        except Exception as e:
            errores += 1
            print(f"  Error en consulta {i+1}: {e}")

    if tiempos:
        print(f"\n  Resultados {endpoint}:")
        print(f"  Media   : {statistics.mean(tiempos):.1f} ms")
        print(f"  Mediana : {statistics.median(tiempos):.1f} ms")
        print(f"  Mínimo  : {min(tiempos):.1f} ms")
        print(f"  Máximo  : {max(tiempos):.1f} ms")
        print(f"  Std Dev : {statistics.stdev(tiempos):.1f} ms")
        print(f"  Errores : {errores}")
        print(f"  R7 cumple (máximo < 2000ms): {max(tiempos) < 2000}")
    return tiempos

tiempos_quote = medir_latencia("/policy/quote", payload_quote, n=100)
tiempos_event = medir_latencia("/event/check",  payload_event, n=100)

print("\n" + "="*50)
print("RESUMEN FINAL — R7 Latencia")
print("="*50)
todos = tiempos_quote + tiempos_event
print(f"  Total consultas  : {len(todos)}")
print(f"  Media global     : {statistics.mean(todos):.1f} ms")
print(f"  Máximo global    : {max(todos):.1f} ms")
print(f"  R7 CUMPLE        : {max(todos) < 2000}")