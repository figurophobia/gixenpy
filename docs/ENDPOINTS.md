# Gixen: el "API" real (protocolo interno)

Gixen **no expone una API JSON pública** para usuarios normales (la API de
suscriptores Mirror está restringida). El sitio es `https://www.gixen.com`,
100% HTML/PHP, sin peticiones XHR/fetch a datos. El "API interna" es, por
tanto, el protocolo de formularios que el propio sitio usa.

Esto documenta ese protocolo, capturado contra una cuenta real
(`POST`/`GET` a `https://www.gixen.com/main/`), para que `gixenpy` hable con
Gixen directamente con `requests` (sin navegador). Es la "versión API pura"
de lo que antes se hacía simulando un navegador.

## ¿Por qué no es un "POST con JSON"?

En algunos servicios hay una API JSON real (endpoints que aceptan y devuelven
JSON, p. ej. `PUT /api/.../reserve` con body `{"reserved": true}`). En
**Gixen no hay ningún endpoint que devuelva o acepte JSON** — lo hemos
comprobado capturando el tráfico del sitio: todo son formularios HTML
servidos por PHP.

Lo más parecido a un "POST con datos" que existe en Gixen es un formulario
clásico (`Content-Type: application/x-www-form-urlencoded`, no JSON), por
ejemplo para añadir un snipe:

```
POST https://www.gixen.com/main/home_2.php?sessionid=142975504960100936
Content-Type: application/x-www-form-urlencoded

newitemid=227503369873&newmaxbid=120.00&username=davidavirn
```

Consecuencia práctica: en Gixen **no hay "API JSON pura" que descubrir**. El
flujo de los formularios es el mismo siempre; la única mejora de rendimiento
disponible es **no volver a hacer login en cada operación**, que es
exactamente lo que aporta la sesión persistente (guardar las cookies en disco
y reutilizarlas). Medido en vivo, saltarse el login reduce cada operación de
~4.5s a ~2.8s de mediana — un ahorro del ~39%, todo de la sesión persistente,
no de "usar API pura".

## Sesión (lo único que hay que mantener)

Gixen permite **una sola sesión por cuenta**. El token de sesión aparece de
dos formas:

- Cookie `PHPSESSID` (establecida por el propio servidor al hacer login).
- Cookie `sessionid` + parámetro `sessionid=<número>` en la query de los
  formularios logueados.

Con las cookies basta para que el servidor te reconozca. `gixenpy` guarda
las cookies en `~/.config/gixenpy/session.json` y las reutiliza, para no
kickearte del navegador con cada operación (ver `client.py`, método
`_save_session` / `_restore_session`).

## Endpoints

Base: `https://www.gixen.com/main/`

| Fichero | Uso | Método real |
|---|---|---|
| `home_1.php` | Login (formulario de login) | `POST` `username`, `password`, `signin=signin`, `Submit=Log in Now` |
| `home_2.php` | Panel principal (lista de snipes + añadir/botones) | `GET`/`POST` con `sessionid` en la query |
| `settings.php` | Preferencias de la cuenta | `POST` `?username=<u>&sessionid=<s>` con el campo de cada preferencia |
| `history.php` | Historial de snipes finalizados (búsqueda) | `GET`/`POST` `?username=<u>&sessionid=<s>&startts=<unix>&keyword=<kw>` |
| `upload.php` | Importar snipes desde CSV | `POST` multipart, campo `file` + `submit=Upload` |
| `status.php`, `groups.php` | **No existen** (404) — verificado en vivo | — |
| `closeaccount.php` | Cerrar cuenta (¡destructivo!) | `POST` |

`status.php` y `groups.php` con `404` significan que Gixen gestiona estados y
grupos dentro del propio `home_2.php`, no como páginas aparte.

## Login (home_1.php → home_2.php)

1. `POST home_1.php` con las credenciales: respuesta `200` con una "bridge
   page" cuyo `meta refresh` apunta a `home_2.php?sessionid=<N>`.
2. `GET home_2.php` (con la cookie que ya tiene `PHPSESSID`): este es el
   panel logueado, donde viven todos los formularios.
3. El `sessionid` numérico se lee de cualquier `action` del panel
   (`home_2.php?sessionid=...`) y se reutiliza como query param.

Al reutilizar la sesión guardada se **evita** el login (que tarda ~4
peticiones y además invalida la sesión del navegador).

## Panel (home_2.php): los formularios

Todos los `action` llevan `?sessionid=<N>`; LOGOUT hace un POST simple.

| `name` del form | Campos | Qué hace |
|---|---|---|
| `addsnipe` | `newitemid`, `newmaxbid`, `username` (+ `newbidoffset`, `newbidoffsetmirror`, `newsnipegroup`) | Añadir snipe |
| `edit…`/`dbidid` | `edititemid`, `editmaxbid`, `ismodified=1`, `dbidid=<id>` | Modificar/borrar un snipe |
| `purge` | `purgecompleted=1`, `gixenlinkcontinue=1` | Limpiar snipes terminados |
| `refreshprices` | `refreshprices=1`, `gixenlinkcontinue=1` | Refrescar precios de los activos |
| `importwatchlist` | `importwatchlist=1` | Importar la Watchlist de eBay |
| `importgixenlist` | `importgixenlist=1` | Importar la lista de Gixen |
| `logmeout` | `logout=1` | Cerrar sesión |
| `settings`, `history` | — | Enlaces internos al panel |

## settings.php: cada preferencia es un form pequeño

Los formularios se distinguen por su atributo **`name`**. Todos POSTean a
`settings.php?username=<u>&sessionid=<s>`.

| `name` del form | Campo(s) | Valores vistos |
|---|---|---|
| `changecountry` | `newcountry` | código numérico (8 = US, …) |
| `changeebaysite` | `newebaysite`, `newebaysitemirror`, `newautotarget` | sitio eBay + target automático |
| `changebidoffset` | `newdefaultoffset`, `newdefaultoffsetmirror` | 6 por defecto |
| `changenumberofgroups` | `newnumberofgroups` | 10 |
| `changenotifications` | `newnotifications` (`t`/`f`), `newemail` | avisos por email |
| `changeshowimages` | `newshowimages` (`1`/`0`) | miniaturas en el panel |
| `addfieldspositionform` | `newaddfieldsposition` (`1`/`0`) | posición de campos |
| `changecontingency` | `newcontingency` (`true`/`false`) | puja de contingencia |
| `changemultiwin` | `newmultiwin` (`true`/`false`), `group1..group10` | puja múltiple por grupo |
| `changecomments` | `newcomment` (`true`/`false`) | enviar comentario al vendedor |
| `changeweakpasswarn` | `newweakpasswordwarning` (`1`/`0`) | aviso de contraseña débil |

Para **leer**: `GET settings.php?...` y parsear el `<select>` (<option
`selected` o el primero). Para **escribir**: POST de los campos que se
quieren cambiar (todos los forms van a la misma URL).

## history.php: el historial de snipes

- El form `searchhistory` POSTea `keyword` a `history.php?username=<u>&sessionid=<s>`.
  **La búsqueda es por TÍTULO**, no por número de item (verificado: `Sony`
  → resultados; `2274435` → 0).
- Paginación: el form `historyolder` POSTea `startts=<unix>&keyword=` para
  la página siguiente.
- Cada fila es un `<tr class="d1">` con celdas de clase `r1..r9`:

| Clase | Contenido |
|---|---|
| `r1` | Número de item de eBay (en un `<a>` a cgi.ebay.es) |
| `r2` | Título (+ vendedor en `<i>`) |
| `r3` | End time (UTC) |
| `r4` | Bid (la puja programada) |
| `r5` | Final price (USD) |
| `r6` | Snipe group |
| `r7` | Status ("WON", "OUTBID", "BID UNDER ASKING PRICE", "ENDED", "SCHEDULED", …) |
| `r8` / `r9` | Time added / time deleted |

## Otros detalles verificados

- Gixen devuelve `HTTP 500` para una sesión expirada/inválida, no solo
   cookie caducada.
- Los formularios `action` pueden ser relativos o absolutos; `gixenpy`
  rechaza cualquier acción fuera de `*.gixen.com` (anti-MITM).
- No hay API privada JSON: los "endpoints" nuevos no descubiertos por las
  libs que simulan navegador son `settings.php`, `history.php`,
  `upload.php` y los botones `importwatchlist`/`importgixenlist`/
  `refreshprices` — todos cubiertos por `gixenpy`.