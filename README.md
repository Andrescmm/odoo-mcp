# Odoo 17 Community ↔ Claude Code mediante MCP

MVP funcional para que Claude Code lea y actualice tareas de **Project** en Odoo 17 Community. La integración expone solo siete operaciones de negocio; no contiene una herramienta genérica de ORM, JSON-RPC, XML-RPC ni `execute_kw`.

## Arquitectura

```text
Claude Code
   │  MCP stdio local o HTTPS central
   ▼
Servidor Python odoo-project-mcp
   │  HTTPS + Bearer scoped key
   ▼
API /mcp/v1/* del módulo project_mcp
   │  ACL + record rules del usuario técnico
   ▼
project.project / project.task / mail.thread
```

Contenido:

- `odoo_addons/project_mcp`: módulo instalable de Odoo 17.
- `server`: servidor MCP Python con las tools `list_projects`, `list_tasks`, `get_task`, `start_task`, `add_comment`, `complete_task` y `verify_task`.
- `.mcp.json.example`: configuración de proyecto para Claude Code.
- `.env.example`: inventario de variables, sin secretos reales.
- `deploy`: despliegue Linux permanente con systemd, HTTPS y tokens personales.

## Modalidades

| Modalidad | Uso |
|---|---|
| `stdio` (predeterminada) | Claude inicia el proceso local para una sola sesión. |
| `streamable-http` | Linux mantiene el MCP activo para varios colaboradores. |

Para un equipo, usa la guía completa [deploy/README.md](deploy/README.md). Incluye un token distinto para cada colaborador, roles, expiración, revocación, rate limiting, Caddy y systemd. El puerto interno queda ligado a `127.0.0.1`; solo se publica HTTPS.

## 1. Instalar el módulo en Odoo

Requisitos: Odoo 17 Community, Python de Odoo y los módulos estándar `project` y `mail`.

1. Copia `odoo_addons/project_mcp` a una ruta incluida en `addons_path`.
2. Reinicia Odoo y actualiza la lista de aplicaciones.
3. Instala **Project MCP** desde Apps, o desde el servidor:

```bash
./odoo-bin -c /etc/odoo.conf -d NOMBRE_DB -i project_mcp --stop-after-init
```

Para actualizar el módulo después de cambios:

```bash
./odoo-bin -c /etc/odoo.conf -d NOMBRE_DB -u project_mcp --stop-after-init
```

En Docker, monta la carpeta `odoo_addons` como un volumen de addons personalizados y agrega esa ruta a `addons_path`.

## 2. Crear el usuario técnico y una clave limitada

1. Crea un usuario interno dedicado, por ejemplo `claude.mcp@example.com`, y asígnale una contraseña local para el alta inicial de la clave. No uses una cuenta administradora.
2. Activa modo desarrollador, abre **Settings → Users & Companies → Groups**, entra en **Project MCP API** y agrega el usuario. Este grupo implica `Project / User`, no `Project / Administrator`.
3. Restringe al usuario con la visibilidad, membresía, compañías y reglas de registro normales de Odoo. La API no usa `sudo()` para proyectos o tareas.
4. Inicia sesión como ese usuario. Ve a **My Profile → Account Security → API Keys** y pulsa **New Project MCP Key**.
5. Guarda la clave una sola vez en un gestor de secretos.

La clave generada por el botón del módulo tiene scope `project_mcp`: autentica estos endpoints, pero no sirve como contraseña de RPC general. El controlador también acepta claves Odoo sin scope por compatibilidad, aunque se recomienda la clave específica.

## 3. Configurar el servidor MCP

El servidor requiere Python 3.10+ y [`uv`](https://docs.astral.sh/uv/). Desde `server`:

```bash
uv sync
```

Exporta las variables en la sesión que iniciará Claude Code:

```bash
export ODOO_BASE_URL="https://odoo.example.com"
export ODOO_DATABASE="odoo17"
export ODOO_LOGIN="claude.mcp@example.com"
export ODOO_API_KEY="la-clave-scoped-copiada-de-odoo"
export ODOO_MCP_AGENT="claude-code"
```

Variables:

| Variable | Obligatoria | Uso |
|---|---:|---|
| `ODOO_BASE_URL` | Sí | URL pública de Odoo, sin `/` final. HTTPS es obligatorio salvo localhost o `ODOO_ALLOW_HTTP=true`. |
| `ODOO_DATABASE` | Solo multidatabase | Inicializa una cookie de selección mediante `/web?db=...` y añade `db` a cada petición. Es preferible configurar también `dbfilter` por host. |
| `ODOO_LOGIN` | Sí | Login esperado para la clave; previene errores de asociación. |
| `ODOO_API_KEY` | Sí | Clave scoped secreta. Nunca se registra ni se devuelve. |
| `ODOO_MCP_AGENT` | No | Nombre guardado en `ai_agent`; por defecto `claude-code`. |
| `ODOO_TIMEOUT_SECONDS` | No | Timeout, 1–120 segundos; por defecto 20. |
| `ODOO_VERIFY_TLS` | No | Verificación TLS; por defecto `true`. No desactivar en producción. |
| `ODOO_ALLOW_HTTP` | No | Permite HTTP fuera de localhost; solo desarrollo en red confiable. |

En modo de equipo se agregan `MCP_TRANSPORT=streamable-http`, `MCP_PUBLIC_URL`, `MCP_USERS_FILE` y las variables descritas en `deploy/server.env.example`. El modo HTTP se niega a iniciar si falta el archivo de colaboradores o si es legible por grupo/otros.

Prueba que el proceso inicia; en `stdio` queda esperando mensajes MCP y no imprime en stdout:

```bash
uv run odoo-project-mcp
```

También se puede instalar con `pip`:

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/odoo-project-mcp
```

## 4. Conectar Claude Code

Copia `.mcp.json.example` como `.mcp.json` en la raíz del proyecto donde está esta carpeta `server`. Claude Code expande las variables `${...}` en la configuración. Para otra ubicación, reemplaza `${CLAUDE_PROJECT_DIR:-.}/server` por una ruta absoluta.

Alternativamente, registra el servidor local desde la carpeta raíz de este repositorio:

```bash
claude mcp add --transport stdio --scope project odoo-project -- \
  uv --directory "$(pwd)/server" run odoo-project-mcp
```

Luego abre Claude Code, aprueba el servidor de proyecto cuando lo solicite y comprueba `/mcp`. Ejemplos de solicitudes:

- “Lista los proyectos de Odoo que contengan Farmacia.”
- “Lee la tarea 58 y márcala en progreso usando la rama `feat/task-58`.”
- “Agrega a la tarea 58 un comentario con los resultados de las pruebas.”
- “Completa la tarea 58 con este resumen y el commit `abcdef1234567`.”
- “Verifica la tarea 58; falló porque falta la prueba de regresión.”

## 5. Probar la API REST con curl

Configura variables de shell. Los ejemplos incluyen `db`; si Odoo usa una sola base con `dbfilter`, puede omitirse.

```bash
export ODOO_URL="https://odoo.example.com"
export ODOO_DB="odoo17"
export ODOO_LOGIN="claude.mcp@example.com"
export ODOO_KEY="la-clave-scoped"
```

Listar proyectos:

```bash
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $ODOO_KEY" \
  -H "X-Odoo-Login: $ODOO_LOGIN" \
  "$ODOO_URL/mcp/v1/projects?db=$ODOO_DB&name=Farmacia&limit=20"
```

Listar tareas:

```bash
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $ODOO_KEY" \
  -H "X-Odoo-Login: $ODOO_LOGIN" \
  "$ODOO_URL/mcp/v1/projects/8/tasks?db=$ODOO_DB&ai_status=pending"
```

Obtener una tarea:

```bash
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $ODOO_KEY" \
  -H "X-Odoo-Login: $ODOO_LOGIN" \
  "$ODOO_URL/mcp/v1/tasks/58?db=$ODOO_DB"
```

Iniciar una tarea:

```bash
curl --fail-with-body --silent --show-error \
  -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ODOO_KEY" \
  -H "X-Odoo-Login: $ODOO_LOGIN" \
  -d '{"agent":"claude-code","git_branch":"feat/task-58"}' \
  "$ODOO_URL/mcp/v1/tasks/58/start?db=$ODOO_DB"
```

Agregar comentario:

```bash
curl --fail-with-body --silent --show-error \
  -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ODOO_KEY" \
  -H "X-Odoo-Login: $ODOO_LOGIN" \
  -d '{"comment":"Pruebas unitarias ejecutadas correctamente."}' \
  "$ODOO_URL/mcp/v1/tasks/58/comments?db=$ODOO_DB"
```

Completar una tarea:

```bash
curl --fail-with-body --silent --show-error \
  -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ODOO_KEY" \
  -H "X-Odoo-Login: $ODOO_LOGIN" \
  -d '{"summary":"Control de lotes implementado y probado.","git_branch":"feat/task-58","git_commit":"abcdef1234567","mark_done":true}' \
  "$ODOO_URL/mcp/v1/tasks/58/complete?db=$ODOO_DB"
```

Verificar una tarea:

```bash
curl --fail-with-body --silent --show-error \
  -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ODOO_KEY" \
  -H "X-Odoo-Login: $ODOO_LOGIN" \
  -d '{"result":"passed","notes":"Criterios de aceptación y regresión aprobados.","update_task_state":true}' \
  "$ODOO_URL/mcp/v1/tasks/58/verify?db=$ODOO_DB"
```

Todas las respuestas usan esta envoltura:

```json
{"ok":true,"request_id":"...","data":{}}
```

Los errores usan el status HTTP correspondiente y `{"ok":false,"error":{"code":"...","message":"..."}}`.

## Semántica de estados

| Operación | Campos MCP | Estado nativo `project.task.state` |
|---|---|---|
| `start_task` | `ai_status=in_progress`, agente, fecha y branch opcional | `01_in_progress` |
| `complete_task` | `ai_status=completed`, resumen, branch/commit y fecha | `1_done` si `mark_done=true` |
| `verify_task(passed)` | `ai_status=verified`, `verification_status=passed` | `1_done` si `update_task_state=true` |
| `verify_task(failed)` | `ai_status=changes_requested`, `verification_status=failed` | `02_changes_requested` si `update_task_state=true` |

Cada mutación agrega una nota interna al chatter. `add_comment` también crea una nota interna (`mail.mt_note`), no un correo al cliente.

## Auditoría y errores

Cada petición genera:

- un registro en `project.mcp.audit.log`, legible por Project Administrators;
- una línea de log de Odoo con request ID, acción, resultado, usuario y IDs de proyecto/tarea;
- el header y campo JSON `request_id` para correlación.

No se guardan bodies, comentarios, resúmenes ni claves en la auditoría. Un autovacuum elimina hasta 10.000 registros por ejecución que superen 90 días. La retención se cambia con el parámetro de sistema `project_mcp.audit_retention_days` (1–3650).

En modo HTTP, el MCP añade a cada llamada interna el identificador y nombre del colaborador autenticado. Odoo los conserva en `actor_identifier` y `actor_name`; el usuario técnico continúa siendo el usuario Odoo que aplica ACL y reglas de registro.

El servidor MCP trata de forma explícita timeouts, errores de red, redirecciones a `/web/login`, respuestas no JSON y errores HTTP. Odoo devuelve detalles útiles para validaciones esperadas y oculta trazas internas en errores inesperados.

## Pruebas automatizadas

Servidor Python:

```bash
cd server
uv sync --extra dev
uv run pytest
uv run ruff check .
```

Módulo Odoo, desde la instalación de Odoo con el addon en `addons_path`:

```bash
./odoo-bin -c /etc/odoo.conf -d DB_PRUEBAS \
  --test-enable --test-tags /project_mcp -i project_mcp --stop-after-init
```

Usa una base desechable para pruebas de Odoo.

## Seguridad antes de producción

- Usa usuario técnico dedicado, scope `project_mcp`, mínimo acceso a proyectos y compañías, y rota la clave periódicamente.
- Termina TLS en Odoo o en un proxy confiable. Mantén `ODOO_VERIFY_TLS=true`.
- Restringe `/mcp/v1/` por IP/VPN en el proxy y aplica rate limiting. No habilites CORS salvo necesidad concreta.
- No pongas `ODOO_API_KEY` directamente en `.mcp.json`, logs, prompts o repositorios. Usa variables de entorno o un gestor de secretos.
- No compartas tokens MCP. En equipo, crea uno por persona y distribúyelo mediante un canal seguro.
- Conserva `proxy_mode` y `dbfilter` correctos en Odoo. En multidatabase, evita que el cliente elija bases que no correspondan al hostname.
- Revisa permisos reales con el usuario técnico: el endpoint aplica ACLs y record rules de Odoo, pero esos permisos siguen siendo responsabilidad del administrador.
- El único `sudo()` del flujo de datos se usa para verificar la clave y escribir auditoría controlada. Las lecturas y cambios de proyectos/tareas se ejecutan como el usuario autenticado.
- La API no crea ni elimina proyectos/tareas, no acepta nombres de modelos o métodos, y no reenvía dominios arbitrarios del cliente.

## Compatibilidad

El módulo usa APIs presentes en Odoo 17: controladores `type="http"`, `request.get_json_data`, `request.make_json_response`, `request.update_env`, API keys scoped y los estados nativos de `project.task`. Para Odoo 18/19 conviene ejecutar la suite y revisar IDs de vistas y cambios de autenticación antes de instalar.
