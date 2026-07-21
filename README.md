# SismoAI World Cloud

Plataforma experimental mundial que ejecuta el motor DTRG por **35 macroregiones tectónicas independientes**, genera un ranking mundial provisional, conserva el estado regional, ejecuta backtesting y publica un panel estático mediante GitHub Pages.

## Principios

- Un IEDC independiente por región; nunca mezcla todo el planeta en un único índice.
- Resultados provisionales visibles desde el inicio.
- `iedc_public` permanece separado hasta superar el gate científico.
- Cobertura, calidad, confianza, razones y fuentes siempre acompañan al resultado.
- Auditoría prospectiva mediante hashes SHA-256, manifiesto y ledger público.
- No emite alertas oficiales ni órdenes de evacuación.

## Operación automática

El workflow único ejecuta:

- **fast** cada 6 horas: actualización incremental USGS, GOES donde existe cobertura y recálculo regional.
- **daily** una vez al día: actualización incremental USGS, GNSS, GOES, catálogo InSAR reciente y cálculo.
- **weekly** una vez por semana: relleno histórico progresivo hasta cinco años, GNSS, catálogo InSAR, cálculo y backtesting.
- **bootstrap** manual: carga inicial mundial controlada para comenzar a operar sin repetir años de descargas en cada ejecución.

Los trabajos se dividen en 5 shards para reducir el tiempo y limitar la presión sobre las fuentes públicas. El estado se guarda en una rama `state` de un solo commit y los resultados verificables se registran en `audit/public_ledger.jsonl`.

## Panel

GitHub Pages publica:

- ranking mundial;
- IEDC provisional por región;
- estado, confianza, cobertura y calidad;
- familias y razones del cambio;
- salud de las fuentes;
- últimos eventos;
- backtest más reciente;
- manifiesto de integridad.

## Fuentes

- USGS FDSN/ComCat.
- Nevada Geodetic Laboratory GNSS.
- NOAA GOES-GLM en las macroregiones configuradas dentro de su cobertura.
- ASF/Sentinel-1 y OPERA-S1 como catálogo; las descargas autenticadas requieren `EARTHDATA_TOKEN`.
- Productos InSAR locales o descargados cuando están disponibles.

## Limitaciones científicas

La operación técnica no demuestra predicción sísmica. Los resultados deben evaluarse prospectivamente y compararse con modelos base. Un índice alto representa desviación estadística respecto a la línea base regional, no la confirmación de un terremoto futuro.
