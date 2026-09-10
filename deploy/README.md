# Despliegue central para un equipo pequeño

Este despliegue mantiene `odoo-project-mcp` activo mediante systemd, publica únicamente HTTPS mediante Caddy y exige un token diferente para cada colaborador. Está pensado para Debian/Ubuntu y tres o pocos usuarios. Si tu servidor ya usa nginx, también se incluye una configuración equivalente.

## Resultado

```text
Claude Code -- HTTPS + token personal --> Caddy :443
                                           |
                                           v
                                  MCP 127.0.0.1:8000
                                           |
                                           v
                                      Odoo HTTPS
```

El token del colaborador autentica contra el MCP. `ODOO_API_KEY` es una credencial distinta, permanece únicamente en Linux y nunca se entrega a los colaboradores.

Este repositorio contiene solamente el servidor MCP y su despliegue. El módulo Odoo `project_mcp` vive en tu repositorio de addons y debe estar instalado o actualizado en Odoo antes de probar una tool.

## Requisitos previos

- Un dominio, por ejemplo `mcp.fizz.software`, con DNS apuntando al servidor.
- Puertos TCP 80 y 443 accesibles para emitir y renovar el certificado.
- Odoo accesible por HTTPS desde Linux.
- El módulo Odoo `project_mcp` versión `17.0.2.0.0` o posterior instalado.
- Git, Python 3.10+, `uv`, Caddy y permisos de administrador en Linux.
- Una clave Odoo `project_mcp` nueva; no reutilices una clave publicada en chats o historiales.

## 1. Clonar e instalar este repositorio

Tu copia de trabajo en macOS está en:

```text
/Users/andres/Documents/odoo-mcp
```

El repositorio remoto configurado es `git@github.com:Andrescmm/odoo-mcp.git`. En una instalación nueva del servidor Linux, crea el usuario del servicio y clona el repositorio en `/opt/odoo-mcp`:

```bash
sudo useradd --system --home-dir /var/lib/odoo-mcp --create-home \
  --shell /usr/sbin/nologin odoo-mcp
sudo git clone https://github.com/Andrescmm/odoo-mcp.git /opt/odoo-mcp
sudo chown -R root:root /opt/odoo-mcp
sudo chmod -R go-w /opt/odoo-mcp
```

Si el repositorio es privado, configura una deploy key de solo lectura en el servidor o copia tu directorio desde la Mac. Este comando se ejecuta en la Mac y reemplaza `USUARIO@SERVIDOR`:

```bash
rsync -av --exclude '.git' --exclude '.DS_Store' \
  /Users/andres/Documents/odoo-mcp/ USUARIO@SERVIDOR:/tmp/odoo-mcp/
```

Después, en Linux:

```bash
sudo install -d -o root -g root -m 755 /opt/odoo-mcp
sudo cp -a /tmp/odoo-mcp/. /opt/odoo-mcp/
sudo chown -R root:root /opt/odoo-mcp
sudo chmod -R go-w /opt/odoo-mcp
```

Instala `uv` siguiendo su documentación oficial y verifica que exista `/usr/local/bin/uv`. Luego instala exactamente las dependencias bloqueadas:

```bash
sudo /usr/local/bin/uv sync --directory /opt/odoo-mcp/server --frozen
```

## 2. Configurar secretos y red

```bash
sudo install -d -o root -g odoo-mcp -m 750 /etc/odoo-mcp
sudo install -d -o odoo-mcp -g odoo-mcp -m 700 /var/lib/odoo-mcp
sudo cp /opt/odoo-mcp/deploy/server.env.example /etc/odoo-mcp/server.env
sudo chown root:odoo-mcp /etc/odoo-mcp/server.env
sudo chmod 640 /etc/odoo-mcp/server.env
sudoedit /etc/odoo-mcp/server.env
```

Reemplaza `mcp.fizz.software`, la URL/base de Odoo, el login y la clave. Conserva `MCP_HOST=127.0.0.1`: el puerto 8000 no debe exponerse a Internet.

## 3. Crear los tres colaboradores

El comando muestra cada token una sola vez y guarda únicamente su SHA-256 en `users.json`.

```bash
sudo -u odoo-mcp /opt/odoo-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-mcp/users.json add andres@gmail.com \
  --name "Andres" --role admin --expires-days 180

sudo -u odoo-mcp /opt/odoo-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-mcp/users.json add desarrollador@gmail.com \
  --name "Desarrollador" --role developer --expires-days 180

sudo -u odoo-mcp /opt/odoo-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-mcp/users.json add revisor@gmail.com \
  --name "Revisor" --role reviewer --expires-days 180
```

Entrega cada token solo a su propietario mediante un canal seguro. Para rotar o revocar:

```bash
sudo -u odoo-mcp /opt/odoo-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-mcp/users.json rotate desarrollador@gmail.com

sudo -u odoo-mcp /opt/odoo-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-mcp/users.json revoke desarrollador@gmail.com
```

Los cambios se recargan automáticamente; no requieren reiniciar el servicio.

Roles:

| Rol | Acceso |
|---|---|
| `reader` | Consultar proyectos, tareas, etapas, miembros y comentarios. |
| `developer` | Todo lo de lectura, más crear/editar tareas, responsables, etapas, dependencias, comentarios y horas. |
| `reviewer` | Leer, comentar y verificar tareas. |
| `admin` | Todas las operaciones disponibles. |

## 4. Instalar systemd

```bash
sudo cp /opt/odoo-mcp/deploy/systemd/odoo-project-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now odoo-project-mcp
sudo systemctl status odoo-project-mcp
```

Ver registros, sin mostrar secretos:

```bash
sudo journalctl -u odoo-project-mcp -f
```

Comprueba el servicio interno. La salud no prueba credenciales de Odoo; una tool real lo hará después.

```bash
curl --fail http://127.0.0.1:8000/healthz
```

## 5. Publicar HTTPS con Caddy

Instala Caddy desde su repositorio oficial. Reemplaza el dominio de ejemplo, instala la configuración y recarga el servicio:

```bash
sudo cp /opt/odoo-mcp/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy obtiene y renueva el certificado TLS automáticamente cuando el DNS y los puertos 80/443 son correctos. La configuración publica solamente `/mcp`; la sonda `/healthz` queda disponible únicamente en `127.0.0.1:8000`.

Verifica desde otra máquina:

```bash
curl -i https://mcp.fizz.software/mcp
```

Debe responder `401 Unauthorized` porque no lleva token. Desde el propio servidor:

```bash
curl --fail http://127.0.0.1:8000/healthz
```

El firewall debe permitir 80/443 y rechazar conexiones externas al 8000.

### Alternativa: nginx

Copia `/opt/odoo-mcp/deploy/nginx.conf.example` a `/etc/nginx/sites-available/mcp.fizz.software`, ajusta el dominio si hace falta y habilítalo. El archivo ya trae `proxy_buffering off` y timeouts largos: el transporte streamable-http deja la respuesta abierta y con el buffering por defecto el cliente parece colgado.

Primero el certificado. El bloque `:443` referencia archivos que todavía no existen, así que se emite antes de habilitar el sitio:

```bash
sudo apt install certbot
sudo certbot certonly --webroot -w /var/www/html -d mcp.fizz.software
```

Se usa `--webroot` y no `--nginx` a propósito: el plugin de nginx necesita un `server` que ya responda por el dominio, y todavía no lo hay. El sitio por defecto de nginx sirve `/var/www/html` en el puerto 80 para cualquier host, que es justo lo que el desafío ACME necesita.

Luego el sitio:

```bash
sudo cp /opt/odoo-mcp/deploy/nginx.conf.example \
  /etc/nginx/sites-available/mcp.fizz.software
sudo ln -s /etc/nginx/sites-available/mcp.fizz.software /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

`certbot` deja su propio timer de renovación; compruébalo con `systemctl list-timers certbot.timer`. Para que nginx tome el certificado renovado sin intervención:

```bash
echo -e '#!/bin/sh\nsystemctl reload nginx' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

En nginx, `/healthz` también queda restringido a loopback. Ejecuta las mismas verificaciones de arriba; desde fuera esa ruta responde `403`.

## 6. Configurar cada Claude Code

Copia el bloque de `claude-http.mcp.json.example` a la configuración del proyecto o registra el servidor como alcance de usuario. Cada colaborador guarda su propio `FIZZ_MCP_TOKEN` en el editor cifrado de entorno Local de Claude Code Desktop.

```bash
claude mcp add-json --scope user odoo-project \
  '{"type":"http","url":"https://mcp.fizz.software/mcp","headers":{"Authorization":"Bearer ${FIZZ_MCP_TOKEN}"}}'
```

Reinicia Claude, abre una sesión Local nueva y solicita: `Usa list_projects de odoo-project`.

## 7. Actualizar el servidor desde GitHub

Actualiza primero el repositorio separado que contiene `project_mcp` y ejecuta la actualización del módulo en Odoo. La versión mínima para estas 20 tools es `17.0.2.0.0`:

```bash
# Ejecuta con las rutas y el usuario reales de tu instalación Odoo.
./odoo-bin -c /etc/odoo.conf -d NOMBRE_DB \
  -u project_mcp --stop-after-init
sudo systemctl restart odoo
```

Después actualiza este servidor. Antes confirma que `git status` esté limpio. El código instalado es administrado por `root`; los secretos y tokens están fuera del repositorio y no se reemplazan con un pull.

```bash
cd /opt/odoo-mcp
sudo git pull --ff-only
sudo /usr/local/bin/uv sync --directory /opt/odoo-mcp/server --frozen
sudo cp /opt/odoo-mcp/deploy/systemd/odoo-project-mcp.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart odoo-project-mcp
sudo systemctl status odoo-project-mcp
```

Comprueba la salud y abre una sesión nueva de Claude Code para que recargue la lista de herramientas:

```bash
curl --fail http://127.0.0.1:8000/healthz
sudo journalctl -u odoo-project-mcp -n 50 --no-pager
```

En `/mcp` deben aparecer 20 tools. `log_work` solo funcionará si el usuario técnico tiene acceso de Timesheets y el proyecto permite registrar horas.

Si cambió el Caddyfile de ejemplo, compáralo con tu archivo real antes de copiarlo: el real contiene tu dominio. No reemplaces automáticamente `/etc/odoo-mcp/server.env` porque contiene secretos.

## Operación y seguridad

- Rota los tokens cada 180 días o inmediatamente si un equipo se pierde.
- Revisa `journalctl` y la vista **Project → Configuration → MCP API Audit** de Odoo.
- El MCP registra identificador, acción, estado e IP, pero nunca el token ni el cuerpo de la tarea.
- La identidad autenticada reemplaza el parámetro libre `agent` en modo HTTP para impedir suplantación.
- El rate limit es por colaborador y también limita intentos inválidos por IP.
- La limitación incluida funciona para un proceso. Si replicas el MCP, mueve rate limiting a nginx (`limit_req`), Redis o el balanceador.
- Tailscale es opcional: puede limitar el dominio a los tres dispositivos, pero conserva igualmente los tokens personales.
