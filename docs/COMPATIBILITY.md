# Compatibilidad comprobada

El gestor inspecciona los ejecutables instalados; no instala versiones de proveedor.

| Componente | Versiones comprobadas | Mecanismo |
|---|---|---|
| Python | 3.12; mínimo de código 3.11 | Biblioteca estándar, `tomllib`, Linux PTY/flock |
| Codex CLI | 0.160.0 / 0.160.1 | app-server local; `account/read`, `account/rateLimits/read`; `sqlite_home`; `--no-daemon` |
| Claude Code | 2.1.284 | `/usage` con `--print --verbose --output-format stream-json`, sin turnos; `usage_report` estructurado |
| Antigravity | 1.3.0 / 1.3.1 | `/usage`, `/credits`, `models` |
| OpenCode | 1.18.34 / 1.18.35 | Catálogo actual; SQLite en sólo lectura para contadores locales |
| VS Code | Adaptador base 1.136.1 / 1.136.2 | Orden de terminales; estado Claude 2.1.258/2.1.284 |

Para otra versión Claude se conserva un PTY acotado, sin completar login,
confianza ni diálogos. Si cambia el formato o falla la consulta, el resultado es
UNKNOWN. Los parsers toleran campos opcionales y ventanas adicionales; no pueden
garantizar formatos futuros de un proveedor.

Las comprobaciones de comandos y permisos no certifican una conversación real
entre identidades ni disponibilidad de un modelo FREE. Las pruebas del gestor
no generan respuestas al modelo para verificar cuotas.

Referencias oficiales: [Codex app-server](https://developers.openai.com/codex/app-server/),
[Claude CLI](https://code.claude.com/docs/en/cli-reference),
[modos de permiso de Claude](https://code.claude.com/docs/en/permission-modes),
[OpenCode](https://opencode.ai/docs/).
