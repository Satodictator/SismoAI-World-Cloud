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
- progreso de la reconstrucción histórica global desde 1973;
- inventario de cobertura real por fuente;
- patrones candidatos con entrenamiento y prueba cronológica posterior separados.

## Fuentes

- USGS FDSN/ComCat.
- Nevada Geodetic Laboratory GNSS.
- NOAA GOES-GLM en las macroregiones configuradas dentro de su cobertura.
- ASF/Sentinel-1 y OPERA-S1 como catálogo; las descargas autenticadas requieren `EARTHDATA_TOKEN`.
- Productos InSAR locales o descargados cuando están disponibles.

### NASA y fuentes contextuales

- NASA Earthdata autentica el acceso utilizado por ASF para productos Sentinel-1/OPERA-S1.
- NASA/JPL Fireball, NOAA SWPC, EMSC, CelesTrak y OpenSky pertenecen a la capa MAM
  local. World Cloud los identifica como controles o fuentes contextuales separadas,
  pero no permite que activen alertas ni alteren automáticamente el IEDC.

## Laboratorio histórico separado

El workflow conserva una base histórica compacta y aislada en la rama `state`.
Reconstruye por bloques el catálogo mundial USGS M≥4.5 desde 1973 y examina tanto
reglas sísmicas como combinaciones acotadas de las familias regionales disponibles.
Cada candidato se ajusta con el 70 % inicial del tiempo y se mide en el 30 % posterior.
Las etiquetas futuras se excluyen de las variables de entrada. Ningún resultado de este
laboratorio cambia V11, V12, MAM, DTRG, el IEDC operativo ni el gate público.

## Limitaciones científicas

La operación técnica no demuestra predicción sísmica. Los resultados deben evaluarse prospectivamente y compararse con modelos base. Un índice alto representa desviación estadística respecto a la línea base regional, no la confirmación de un terremoto futuro.
