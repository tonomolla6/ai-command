# Contrato de versiones, actualizaciones y migraciones

## Versiones

- SemVer estable: `vMAJOR.MINOR.PATCH`; `VERSION` y `ai_manager.__version__` deben coincidir.
- Patch: correcciones compatibles. Minor: nuevas funciones y migraciones compatibles.
- Major: cambio incompatible documentado; no eliminar perfiles ni historial.
- Tags publicados inmutables: corregir con una versión nueva, nunca mover un tag.
- `ai update` consulta la última GitHub Release estable, que debe tener paquete y SHA256SUMS.

## Publicar una versión

1. Cambiar VERSION, `ai_manager/__init__.py` y CHANGELOG; añadir pruebas del cambio.
2. Commit de cada unidad terminada; árbol limpio antes de etiquetar.
3. Ejecutar tests Python/Node y `scripts/build_release.py --out <directorio-temporal>`.
4. Revisar `git ls-files` y el inventario del tar: sólo código y documentación pública.
5. Verificar identidad GitHub y publicar rama/tag; crear release con el tar y SHA256SUMS.
6. Probar `ai update --check` y un update real desde la release anterior en un HOME aislado.

En el workspace del autor, usar su verificador de identidad antes de cualquier push.
El workflow de CI sólo valida; publicar la release es una operación explícita del mantenedor.

## Instalación

```text
PREFIX/lib/ai-command/
  current -> releases/v1.0.0-HASH/
  releases/v1.0.0-HASH/
  native/                   # enlaces a los originales del proveedor
  backups/                  # lanzadores/metadatos previos
  install.json
  .install.lock
PREFIX/bin/ai               # lanzador estable
```

El paquete contiene Python/JS/shell y no depende de la arquitectura de los CLI.
Cada instalación usa su Python >=3.11, comprueba el paquete y hace un smoke test antes
de activar. El update verifica SHA-256 de SHA256SUMS y el digest de GitHub cuando existe.
Esto detecta corrupción y paquetes distintos; el mantenedor y GitHub siguen siendo
la raíz de confianza. No hay firma criptográfica independiente en v1.

La extracción rechaza rutas absolutas, escapes `..`, enlaces y tamaños excesivos.
La activación/reversión usa un enlace atómico y lock por instalación. Se conservan
versiones anteriores, sin limpieza por edad ni poda de datos del proveedor.

## Migraciones de datos

Las funciones de `ai_manager/migrations.py` reciben una copia del registro del
gestor, conservan campos desconocidos y aplican pasos en orden. Añade un paso por
versión de esquema; no cambies el significado de pasos publicados. Antes de escribir,
se guarda backup privado y se usa escritura atómica. Prueba ejecución repetida,
fallo antes de escribir, preservación de campos y rechazo de esquema futuro.

El esquema de metadatos `manager_schema` es distinto de `schema` del registro de
cuentas. Ninguna migración debe editar `auth.json`, `.credentials.json`, bases,
transcripts, Git ni configuración de organización. No incluir llamadas al modelo,
flows OAuth o efectos externos en una migración.

Las migraciones se ejecutan al usar `ai`, por HOME. Una actualización global no
abre ni migra los homes de otros usuarios. `ai migrate --dry-run` muestra el plan.
El paso 001 conserva la política de bypass del gestor pre-release; una instalación
nueva con `ai setup --empty` comienza con permisos normales.

## Rollback

`ai update --rollback` valida checksums de la versión anterior y que admite el
esquema actual; no restaura tokens ni datos del proveedor. Si una futura migración
no admite downgrade, rechaza el rollback y conserva la versión activa. Una
recuperación de configuración desde backup debe ser explícita y documentada.

## Nuevo servidor / nuevas integraciones

Instala el mismo tag y crea los perfiles con `ai add`/`ai login`. Copiar logins o
historial exige una operación privada separada autorizada; GitHub nunca los recibe.
Los CLI oficiales siguen siendo dependencias externas y no se actualizan mediante ai.

Para nuevos proveedores añade un adaptador, formato de caché compatible, prueba
sin modelo y documentación de UNKNOWN. Para nuevas extensiones VS Code cambia su
versión en package.json y ejecuta `ai vscode-install` después del update; no
sobrescribas una extensión de la misma versión con cambios locales.
