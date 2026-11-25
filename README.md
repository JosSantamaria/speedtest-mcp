# speedtest-mcp

Stack Docker para monitorear la conexión a internet (ISP Telmex 100/40 Mbps) y exponerlo como herramientas MCP para agentes de IA (Kiro). Desplegado en PRX1 (10.0.2.3) vía Coolify.

## Componentes

- **Speedtest Tracker** (`ghcr.io/alexjustesen/speedtest-tracker:latest`): realiza pruebas automáticas de velocidad cada 60 minutos, expone una UI web y una API REST.
- **MCP Server** (Python 3.12 / fastmcp 2.0): consume la API de speedtest-tracker y expone 4 herramientas MCP para que agentes de IA (Kiro, Claude, etc.) puedan consultar resultados en tiempo real.

## Prerrequisitos: construir la imagen MCP

El host Proxmox donde corre Coolify tiene BuildKit roto. La imagen del MCP server se construye a mano **antes** de hacer deploy:

```bash
# Ejecutar en PRX1 (10.0.2.3), desde el directorio del repo
DOCKER_BUILDKIT=0 docker build -t mcp-speedtest-local:latest ./mcp-server
```

## Variables de entorno

Copiar `.env.example` a `.env` y completar:

| Variable | Descripción | Valor por defecto |
|---|---|---|
| `APP_KEY` | Clave de Laravel para speedtest-tracker | *(generar)* |
| `DB_CONNECTION` | Motor de base de datos | `sqlite` |
| `TZ` | Zona horaria | `America/Merida` |
| `UI_PORT` | Puerto de la UI web en el host | `8083` |
| `MCP_PORT` | Puerto del servidor MCP en el host | `3100` |
| `SPEEDTEST_SCHEDULE` | Cron de pruebas automáticas | `0 * * * *` |
| `SPEEDTEST_API_KEY` | API key de speedtest-tracker | *(opcional al inicio)* |

### Generar APP_KEY

```bash
# Opción 1 (openssl, recomendado)
echo "base64:$(openssl rand -base64 32)"

# Opción 2 (si tienes php instalado)
php artisan key:generate --show
```

### Obtener SPEEDTEST_API_KEY

1. Iniciar el stack sin la key: `docker compose up -d`
2. Acceder a `http://10.0.2.3:8083`
3. Ir a **Settings → API → Create API Key**
4. Copiar la key y agregarla al `.env`: `SPEEDTEST_API_KEY=tu_key_aqui`
5. Reiniciar el MCP server: `docker compose restart mcp-server`

> Si speedtest-tracker no tiene la API habilitada por defecto, el MCP server puede funcionar sin key inicialmente (todos los endpoints son públicos en ese caso).

## Despliegue en Coolify

1. En Coolify, crear nuevo Resource → **Docker Compose**.
2. Pegar el contenido de `docker-compose.yml` en modo **Raw**.
3. Habilitar **"Isolated network only"** para evitar que Coolify inyecte la red `coolify` (10.210.1.0/24).
4. Configurar las Environment Variables copiando el contenido de `.env.example` y completando los valores.
5. **ANTES de hacer Deploy**, construir la imagen MCP a mano en PRX1 (ver sección Prerrequisitos).
6. Hacer Deploy y verificar:
   - UI: `http://10.0.2.3:8083`
   - MCP SSE: `http://10.0.2.3:3100/sse`

## Integración con Kiro/MCP

Agregar al archivo `.kiro/settings/mcp.json` del workspace de Kiro:

```json
{
  "mcpServers": {
    "speedtest": {
      "type": "sse",
      "url": "http://10.0.2.3:3100/sse"
    }
  }
}
```

**Herramientas disponibles:**

| Herramienta | Descripción |
|---|---|
| `get_latest_result()` | Último resultado: download_mbps, upload_mbps, ping_ms, jitter_ms, timestamp, server_name |
| `get_history(days=7)` | Lista de resultados de los últimos N días |
| `run_speedtest()` | Dispara una prueba manual y espera el resultado (30-60 s) |
| `compare_vs_plan(expected_down=100.0, expected_up=40.0)` | Promedios de 7 días vs plan del ISP, con % de cumplimiento |

## IMPORTANTE: Excepción de QoS en el MikroTik

El router MikroTik RB760iGS tiene QoS activa que limita el throughput de **todo** el tráfico:

- Cola simple **CAKE-QoS-Bufferbloat**: 88 Mbps bajada / 35 Mbps subida
- Cola tree **UPLOAD-CAKE-WAN**: 34 Mbps en ether1

**Sin esta excepción, las pruebas de speedtest se toparán en ~88/34 Mbps en lugar de lo que entrega realmente el ISP (plan contratado: 100/40 Mbps). Los resultados no serán representativos del ISP.**

### Agregar excepción (ejecutar en el router MikroTik)

```
# 1. Agregar la IP de PRX1 a la address-list de bypass
/ip firewall address-list
add address=10.0.2.3 list=QOS-BYPASS comment="PRX1 - Speedtest bypass QoS"

# 2. Regla mangle: detener procesamiento de marks para PRX1
#    action=accept passthrough=no hace que el paquete salte todas las reglas
#    de mangle siguientes, impidiendo que reciba marcas para las colas.
#    place-before=0 coloca la regla ANTES de cualquier otra regla de mangle.
/ip firewall mangle
add chain=prerouting src-address-list=QOS-BYPASS action=accept passthrough=no \
    comment="QoS-BYPASS PRX1 speedtest" place-before=0

# Verificar que quedó en posición 0 (primera regla):
/ip firewall mangle print
```

### Revertir excepción

```
/ip firewall mangle remove [find comment="QoS-BYPASS PRX1 speedtest"]
/ip firewall address-list remove [find where list=QOS-BYPASS]
```

> **Nota técnica**: `action=accept passthrough=no` en la cadena `prerouting` de mangle detiene el procesamiento de las reglas de mangle para ese paquete. Los paquetes de/hacia 10.0.2.3 no recibirán marcas, por lo que las colas tipo **queue tree** (que dependen de packet marks) no los procesarán. Para **simple queues** que usan `target` por rango de IP directamente (en lugar de marks), puede ser necesario verificar con `/queue simple print` si el target incluye 10.0.2.3; en ese caso habría que ajustar el target de la cola para excluirla.

## Verificación post-arranque

```bash
# Estado de los contenedores
docker compose ps

# Logs en tiempo real
docker compose logs -f

# Verificar API de speedtest-tracker (devuelve último resultado)
curl http://10.0.2.3:8083/api/v1/results/latest

# Verificar que el MCP server responde
curl http://10.0.2.3:3100/sse

# Disparar prueba manual via API (requiere API key si está habilitada)
curl -X POST http://10.0.2.3:8083/api/v1/speedtests \
  -H "Authorization: Bearer ${SPEEDTEST_API_KEY}" \
  -H "Content-Type: application/json"

# Validar docker-compose.yml en el servidor
docker compose config
```

## Notas de red

- La red Docker `backend` es interna al stack. El MCP server alcanza speedtest-tracker como `http://speedtest:80`.
- No usar la red `coolify` (10.210.1.0/24). El compose declara la red `backend` explícitamente y Coolify usa modo Isolated.
- El MCP server **no requiere autenticación** (uso interno en LAN 10.0.1.0/24).
- El MCP server **no debe exponerse a internet**; solo accesible desde la LAN.
- El schedule de 60 min es suficiente para detectar degradación del ISP sin saturar la línea constantemente.
