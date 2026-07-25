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
- un boletín determinista en cada actualización que explica qué cambió, qué regiones
  se apartan de su referencia y cuáles son las limitaciones;
- texto y lectura por voz en español, inglés, portugués, francés, italiano, alemán,
  japonés, turco, griego e indonesio, seleccionados por el idioma del navegador y
  con selección manual;
- archivo público de boletines recientes para conservar la explicación de cada ejecución.

## Boletín explicativo y voz

El boletín se construye exclusivamente con los resultados verificados de cada ejecución y
los compara con el estado mundial anterior. No utiliza texto libre generado por IA ni
inventa causas. Distingue actividad observada, anomalía estadística, patrón candidato y
señal experimental.

La reproducción usa las voces disponibles en el navegador o dispositivo del visitante.
Nunca comienza automáticamente: el visitante debe pulsar **Escuchar boletín**. Si el
dispositivo no tiene una voz para el idioma seleccionado, el texto traducido permanece
disponible.

Una señal experimental solo puede mostrarse si están simultáneamente aprobados el gate
público, la línea base completa, tres familias independientes, confianza mínima del 75 %,
validación prospectiva, probabilidad calibrada, tasa de falsas alarmas aceptable y todos
los campos científicos de la señal. Incluso entonces se presenta como
**SEÑAL EXPERIMENTAL PRIORITARIA — NO ES ALERTA OFICIAL**.

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


## Próxima etapa científica: ventanas probabilísticas en modo sombra

La memoria histórica mundial se reconstruye progresivamente desde 1973. El estado registrado el 24 de julio de 2026 era de **24 de 643 meses (3,7 %)**. En ese momento se estimó que, si las ejecuciones continuaban sin errores y con velocidad similar, el catálogo podría completarse aproximadamente entre el **30 de julio y el 2 de agosto de 2026**. Esta estimación debe recalcularse con el estado real y no es una garantía.

Después de completar y verificar el histórico, el siguiente paso previsto es iniciar ventanas experimentales de **24 horas, 72 horas, 7 días y 30 días**, exclusivamente en **modo sombra**. Las ventanas deberán:

- registrarse antes de conocer el futuro;
- compararse con la frecuencia sísmica normal de cada región;
- conservar versión, variables, fecha, fuentes y SHA-256;
- evaluarse al cierre sin reescribir el pronóstico original;
- mostrar aciertos, fallos, falsas alarmas, omisiones y calibración;
- compararse con modelos base sencillos;
- permanecer separadas de V11, V12, MAM, IMCP, DTRG operativo e IEDC público;
- no activar alertas ni órdenes de evacuación.

Ejemplo de presentación, con cifras únicamente ilustrativas:

```text
Ventana experimental: 3–10 de agosto de 2026.
Posibilidad calculada de un evento M≥5 en Centroamérica: 8 %.
Nivel habitual de referencia: 2 %.
Confianza o calidad estimada: 76 %.
No es una predicción ni una alerta oficial.
```

El USGS distingue entre predicción exacta, pronóstico probabilístico y alerta temprana. La alerta temprana detecta un terremoto que ya comenzó; no predice un evento días antes. CSEP desarrolla evaluación rigurosa y prospectiva de modelos de pronóstico.

Referencias:

- USGS, predicción sísmica: https://www.usgs.gov/faqs/can-you-predict-earthquakes
- USGS, diferencias entre alerta temprana, pronóstico, probabilidad y predicción: https://www.usgs.gov/faqs/what-difference-between-earthquake-early-warning-earthquake-forecasts-earthquake-probabilities
- USGS, alerta temprana: https://www.usgs.gov/programs/earthquake-hazards/science/earthquake-early-warning-overview
- CSEP Testing: https://cseptesting.org/

Calendario responsable:

- **Al completar el histórico:** iniciar ventanas internas en modo sombra.
- **Después de 3–6 meses:** considerar una sección experimental separada solamente si existen muestras y pruebas suficientes.
- **Después de 12–24 meses:** realizar una evaluación prospectiva más seria.
- **Alerta pública:** únicamente si se supera un gate estricto; puede que nunca se supere.

Los patrones candidatos actuales son preliminares. No justifican fechas exactas, avisos de peligro ni evacuaciones.
