# SismoAI Universal Sensor Gateway

## Alcance

Esta capa incorpora fuentes abiertas o expresamente autorizadas sin reemplazar el motor mundial, el laboratorio histórico, las ventanas prospectivas ni el motor evolutivo.

Todas las observaciones:

- se normalizan a UTC;
- se deduplican mediante huella estable;
- conservan fuente, nodo, licencia, calidad y latencia;
- se asignan a una macroregión cuando existen coordenadas;
- se separan por rol científico;
- permanecen fuera del IEDC, las alertas y las ventanas hasta superar validación.

## Roles

- `PRE_EVENT_RESEARCH`: señales que pueden estudiarse antes del objetivo futuro.
- `EVENT_DETECTION`: sensores que detectan movimiento cuando el evento ya comenzó.
- `TSUNAMI_CONFIRMATION`: presión y nivel del mar para confirmar perturbaciones.
- `CONTEXT_CONTROL`: variables para descartar explicaciones atmosféricas, geomagnéticas o técnicas.

El motor evolutivo solo puede recibir, después del mínimo histórico configurado, características de `PRE_EVENT_RESEARCH` y `CONTEXT_CONTROL`. Las familias de detección y tsunami se excluyen para evitar fuga temporal.

## Fuentes automáticas abiertas

- Inventario de estaciones sísmicas EarthScope FDSN.
- Inventario comunitario Raspberry Shake FDSN.
- Mareógrafos y nivel del mar NOAA CO-OPS.
- Estaciones DART y altura de columna de agua NOAA NDBC.
- Inbox JSONL para nodos propios o autorizados.

FDSN se utiliza para inventario por lotes. No se usa para sondear ondas continuas, porque las corrientes en tiempo casi real deben recibirse mediante SeedLink.

## Fuentes registradas que requieren acceso adicional

- EarthScope SeedLink.
- EarthScope NTRIP GNSS.
- Ocean Networks Canada.
- NIED MOWLAS.
- IOC/GLOSS.
- INTERMAGNET.
- Teléfonos fijos, Raspberry Pi, cámaras, hidrófonos e infrasonido.
- Fibra DAS y cables submarinos SMART.

El registro de una fuente no implica que SismoAI tenga permiso de acceso. El estado público muestra si falta licencia, secreto, convenio o agente persistente.

## Agente persistente

GitHub Actions no mantiene conexiones continuas. `sismoai_world.sensor_edge_agent` proporciona un receptor autenticado que puede ejecutarse en un VPS, mini-PC o Raspberry Pi.

Ejemplo local:

```powershell
$env:SENSOR_EDGE_SHARED_TOKEN = "UNA_CLAVE_LARGA_Y_ALEATORIA"
python -m sismoai_world.sensor_edge_agent serve --host 127.0.0.1 --port 8765 --spool-dir sensor_inbox
```

Para exposición por Internet debe colocarse detrás de HTTPS o VPN. El servidor integrado no sustituye TLS.

## Formato de observación

```json
{
  "source_id": "SISMOAI_PHONE_NETWORK",
  "node_id": "identificador-autorizado",
  "family": "PHONE_IMU",
  "role": "EVENT_DETECTION",
  "observed_at": "2026-07-29T00:00:00Z",
  "measurement": "acceleration_peak",
  "value": 0.012,
  "unit": "m/s2",
  "sample_rate_hz": 100,
  "quality": 0.75,
  "latitude": 10.48,
  "longitude": -66.90,
  "privacy": "PRIVATE"
}
```

## Seguridad y privacidad

- No se aceptan nodos sin autorización.
- Los nodos privados se publican con identificador anonimizado.
- Las coordenadas privadas se redondean.
- Los secretos se almacenan únicamente como GitHub Secrets o variables de entorno.
- Ningún token se escribe en el sitio público.
- Los datos crudos tienen retención limitada; las características agregadas se conservan para investigación.
