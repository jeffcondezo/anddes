# Librerías frontend (terceros)

Registro de dependencias JS/CSS de terceros en la UI HTTP.
Complementa el inventario Python en [librerias.md](librerias.md).

| Librería | Versión | Licencia | Origen en runtime | Archivo |
|----------|---------|----------|-------------------|---------|
| Chart.js | **4.4.6** | MIT | `'self'` | `maestro/static/maestro/js/chart.umd.js` |
| Montserrat | fontsource **5.2.5** (glyphs) | SIL OFL 1.1 | `'self'` | `maestro/static/maestro/fonts/*.woff2` + `css/fonts.css` |

SHA-384 Chart.js: `sha384-MH1axGwz/uQzfIcjFdjEfsM0xlf5mmWfAwwggaOh5IPFvgKFGbJ2PZ4VBbgSYBQN`

## Chart.js

- Uso: gráficos de Indicadores.
- UMD oficial `chart.js@4.4.6` (`dist/chart.umd.js`).
- Sin CDN; CSP no permite jsDelivr.

## Montserrat

- Tipografía de UI (alineada a marca Anddes; alternativa libre a Neo Sans).
- WOFF2 latin + latin-ext, pesos 400/500/600/700.
- Licencia: `static/maestro/fonts/OFL-Montserrat.txt`.
- **No** se descarga desde Google Fonts (evita enviar IP de usuarios a terceros y permite CSP solo `'self'` en `font-src` / `style-src`).

## Neo Sans

Pendiente de archivos con licencia corporativa Anddes. Hasta entonces la UI usa Montserrat self-host.

## Notas CSP

- Scripts con `nonce="{{ csp_nonce }}"`.
- Fuentes y Chart.js desde estáticos propios → no hace falta abrir hosts de Google ni jsDelivr.
