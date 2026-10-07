# Changelog

## 1.0.3 — 2026-10-07

- Nombres e identificadores genéricos en las integraciones de VS Code.
- Control de contenido público en CI y antes de construir paquetes: archivos privados, correos reales y credenciales reconocibles.
- Ejemplos y capturas con datos ficticios; configuración y autenticación permanecen fuera de las releases.

## 1.0.2 — 2026-10-07

- Suite de pruebas portable sin Codex/Claude instalados y cierre explícito de fixtures SQLite en Python 3.13.
- CI valida Python 3.11, 3.12 y 3.13 sin duplicar ejecuciones al publicar un tag.

## 1.0.1 — 2026-10-07

- La migración de una instalación anterior conserva sus ejecutables nativos aunque no estén directamente en PATH.
- Añadida prueba de actualización desde el layout anterior usado en servidores compartidos.
- Las consultas y lanzamientos gestionados de AGY desactivan su auto-update nativo para conservar los binarios compartidos.

## 1.0.0 — 2026-10-07

- Primera versión pública: cuentas por correo, perfiles aislados y atajos dinámicos.
- Codex y Claude automáticos abren sesión nueva; resume explícito por ID/directorio.
- Uso en paralelo con caché, tres columnas, resets, créditos y dependencias agotadas.
- Antigravity, catálogo FREE y actividad local aproximada de OpenCode.
- Handoff seguro mediante estado Git y notas del usuario.
- Instalación por usuario/global, releases con SHA-256, activación atómica y rollback.
- Migraciones privadas, idempotentes y compatibles con la instalación anterior.
- Política opt-in de bypass Claude, incluida reanudación con flags nativos.
- Extensiones locales VS Code y skill para gestionar la herramienta.
