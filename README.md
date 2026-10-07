# Radar de movimientos especulativos (acciones EE. UU.)

Detecta la **coincidencia objetiva de señales** que suele preceder a subidas
especulativas fuertes en small caps:

REVERSE SPLIT + FLOAT PEQUEÑO + VOLUMEN ANORMAL + CATALIZADOR + PRIMER MOVIMIENTO DEL PRECIO

No predice ni recomienda. Los niveles son solo niveles de coincidencia de señales.
Todo queda registrado para comprobar después, con datos, qué señales funcionaron.

## Instalación

```bash
python -m pip install -r requirements.txt
```

Copiar `.env.example` a `.env` y rellenar:

- `SEC_USER_AGENT` (obligatorio): la SEC exige un contacto real, p. ej. `MiRadar/1.0 nombre@dominio.com`.
- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` (opcional, ver abajo).

`.env` contiene secretos: no se comparte ni se sube a git (ya está en `.gitignore`).

## Telegram

1. En Telegram, abrir **@BotFather** → `/newbot` (recomendado un bot solo para este radar) y copiar el token.
2. Pegar el token en `.env` → `TELEGRAM_BOT_TOKEN=...`
3. Escribir cualquier mensaje al bot desde tu cuenta.
4. Ejecutar `python -m radar telegram-setup` → muestra tu `chat_id`. Copiarlo a `.env` → `TELEGRAM_CHAT_ID=...`
5. Probar: `python -m radar telegram-test`

Si el bot ya lo usa otra automatización con webhook, `telegram-setup` lo indica:
usar el `chat_id` que ya tenga esa automatización o crear un bot nuevo.

## Uso

| Comando | Qué hace |
|---|---|
| `python -m radar run` | Bucle continuo: premarket cada 10 min, mercado cada 5 min, resultados cada 30 min |
| `python -m radar scan` | Un ciclo ahora (fase según la hora de Nueva York) |
| `python -m radar dashboard` | Panel en http://127.0.0.1:8765 |
| `python -m radar outcomes` | Actualiza la evolución posterior de las alertas |
| `python -m radar report` | Estadísticas: qué señales han precedido subidas reales |
| `python -m radar refresh-splits` | Fuerza la actualización de reverse splits desde la SEC |

Dos tipos de aviso:

1. **POSIBLE MOVIMIENTO ESPECULATIVO** (ciclo completo: premarket y after-hours cada
   10 min, mercado cada 5 min): la preparación, cuando coinciden las señales.
2. **ARRANQUE DE MOVIMIENTO** (vigilancia rápida cada 90 s de los valores en ALERTA
   ALTA/MÁXIMA): sube ≥ 8 % en ≤ 10 min con volumen real (≥ 5 % de su media diaria).
   Como mucho uno por valor y hora. Todo configurable en `fastwatch`.

Cada aviso indica la hora de Nueva York y de España, y si Trade Republic está
abierto en ese momento (L-V 7:30-23:00 hora de España).

Horario: premarket desde las 04:00 ET (10:00 hora de España peninsular),
apertura 09:30 ET (15:30 España). Dejar `run` abierto en una terminal; el panel
en otra.

## Sin tener el PC encendido (GitHub Actions)

El repositorio es público, así que GitHub Actions no tiene límite de minutos.
`.github/workflows/radar.yml` ejecuta `python -m radar run` en la nube por tandas de
hasta ~5,7 h (GitHub corta los trabajos a las 6 h): ciclo completo cada 5 min en
sesión (10 min en premarket y after-hours) y ARRANQUE cada 90 s. Como GitHub retrasa
o se salta disparos programados, se dispara cada 30 min; solo corre una tanda a la
vez y la siguiente espera en cola. Fuera de horario cada tanda termina en ~1 min.

El histórico (`radar.db`) se guarda en la rama `radar-data` al terminar cada tanda.
Las claves van en Settings → Secrets and variables → Actions:
`SEC_USER_AGENT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (nunca son visibles).

No usar a la vez `run` en el PC y GitHub Actions: llegarían avisos duplicados.

## Cómo funciona un ciclo

1. **Universo**: screener de Nasdaq (NASDAQ, NYSE, AMEX), solo acciones ordinarias
   con precio 0,05–20 $ y capitalización < 500 M$ (configurable).
2. **Preselección**: volumen/media ≥ 2x o subida ≥ 8 %, o reverse split reciente
   (umbrales relajados), máximo 80 valores por ciclo.
3. **Enriquecimiento** de cada candidato, con fuente y fecha por dato.
4. **Puntuación** 0–100 y nivel.
5. **Alerta** si ≥ 65, con control de repetición.
6. **Registro** de todo en `data/radar.db` (SQLite).

## Solo acciones disponibles en Trade Republic

Con `broker.only_tradable: true` (por defecto) cada candidato se busca en
LS Exchange, donde Trade Republic ejecuta las acciones de EE. UU., por ISIN y por
ticker. Si no aparece, se descarta. El resultado se guarda 7 días. Es una
aproximación: si ves en la app una acción que el radar descarta, añádela a
`broker.always_allow`; si quieres ocultar alguna, a `broker.always_block`.
Si la consulta falla, la acción NO se descarta (se marca "no comprobado").

## Puntuación

| Señal | Puntos | Criterio |
|---|---|---|
| Reverse split | 0–20 | Ejecutado > anunciado > propuesto; más reciente y mayor ratio, más puntos |
| Float | 0–20 | < 1M: 20 · < 5M: 16 · < 10M: 12 · < 20M: 6 · < 50M: 2 |
| Volumen | 0–25 | ≥ 10x: 25 · ≥ 5x: 20 · ≥ 3x: 14 · ≥ 2x: 6 · +5 si el premarket ya supera el 50 % de la media diaria |
| Catalizador | 0–20 | Impacto alto/medio/bajo + bonus si es oficial (SEC / nota de prensa) y si es de < 24 h |
| Precio | 0–15 | ≥ 30 %: 9 · ≥ 20 %: 7 · ≥ 10 %: 5 · +3 rompe máximo anterior · +2 aceleración de volumen · premarket ≥ 10 %: +3 |

Reglas contra falsos positivos:

- Con menos de **3 señales activas** la puntuación se limita a 64 (nunca alerta).
  Subir mucho no basta.
- **Bonus de confluencia**: reverse split + float + volumen activos: +8; si además hay catalizador: +15.
- Volumen alto con caída ≥ 10 % (p. ej. tras una ampliación) no cuenta como señal.
- **Llegar tarde**: se mide la subida desde el mínimo de las últimas 5 sesiones.
  ≥ +100 %: −10 puntos ("movimiento avanzado"); ≥ +200 %: nunca pasa de VIGILAR
  (marca TARDE). El objetivo es el comienzo del movimiento, no perseguirlo.
- Datos sospechosos de no estar ajustados por split bloquean la alerta (ver abajo).

Niveles: 80–100 ALERTA MÁXIMA · 65–79 ALERTA ALTA · 50–64 VIGILAR · < 50 SIN ALERTA.

Todos los pesos y umbrales están en `config.yaml`.

## Protección contra datos sin ajustar por split

Tras un reverse split 1:N algunas fuentes gratuitas tardan en ajustar:

- **Cierre anterior sin ajustar** → subida falsa de (N−1)×100 %. El sistema cruza el
  cierre anterior entre Nasdaq, el histórico de Yahoo y el cierre de Yahoo; si difieren
  en el ratio del split conocido (o en ≥ 1:5 sin split conocido) usa el ajustado. Si hay
  un salto ≥ x3 compatible con un split que no está confirmado, el dato queda
  **bloqueado** (marca VERIFICAR en el panel, sin alerta).
- **Volumen antiguo sin ajustar** → media 20 días inflada N veces y ratio falseado.
  Antes de calcular la media se detecta el salto de precio (o la desproporción del
  volumen) en la fecha del split y el volumen previo se divide entre N.
- **Históricos parcialmente ajustados** ("islas" de varias sesiones sin ajustar en
  medio de datos ajustados, visto en VWAV): cada salto del tamaño de un split, al alza
  o a la baja, se corrige por separado para que la serie sea continua.
- **Acciones en circulación / float anteriores al split**: se dividen entre N, y se
  descartan cifras de la SEC con más de 400 días.

Cada ajuste queda anotado en "Calidad de datos" del ticker.

## Fuentes (por prioridad)

| Dato | Fuente |
|---|---|
| Reverse splits anunciados/propuestos, ratio y fecha efectiva | SEC EDGAR (búsqueda de texto completo en 8-K, 6-K, DEF/PRE 14A) |
| Reverse splits ejecutados | Yahoo Finance (histórico de splits), enlazado al 8-K de la SEC |
| Catalizadores | SEC (8-K por items y texto, 6-K, 424B/S-1/S-3, 10-Q/10-K, 13D/13G…), notas de prensa (GlobeNewswire, PR Newswire, Business Wire, Accesswire…) y titulares clasificados |
| Acciones en circulación | SEC XBRL (portada del 10-Q/10-K), Yahoo como alternativa |
| Float | Yahoo Finance (la fuente no publica su fecha de referencia; se indica) |
| Precio/volumen en sesión | Nasdaq screener; barras de 5 min de Yahoo para premarket, máximos y aceleración |
| Media de volumen 20 días | Histórico diario de Yahoo validado contra splits |

Si un dato no existe se muestra **N/D**. Nunca se inventa.

## Histórico y validación

Tabla `alerts`: cada alerta enviada y la primera vez al día que un valor entra en
VIGILAR (no notificada, sirve de comparación). Para cada una se guarda puntuación
de cada señal, precio, y después la **subida máxima a 1 h, 4 h, 1 día y 5 sesiones**
y la caída máxima a 5 sesiones.

`python -m radar report` (o la pestaña "¿Funcionan las señales?" del panel)
compara, por nivel, por señal activa/inactiva y por número de señales, la
subida media y el porcentaje de acierto (umbral configurable, 20 % por defecto).
Usarlo tras varias semanas para ajustar pesos en `config.yaml`.

## Limitaciones conocidas

- Fuentes gratuitas: posible retraso de datos y ciclos de 5 minutos; una subida muy
  rápida puede empezar antes del aviso. Un proveedor de pago en tiempo real
  (Polygon, Finnhub, etc.) encajaría sustituyendo `radar/providers/`.
- En premarket el screener de Nasdaq aún muestra el día anterior; se analizan los
  valores con reverse split reciente, los detectados el día anterior y la lista
  `extra_watchlist`.
- Yahoo suele dar volumen 0 en horario extendido; entonces el volumen premarket es N/D.
- La clasificación de noticias es por palabras clave: objetiva pero imperfecta.

## Tests

```bash
python -m pytest --cov=radar
```
