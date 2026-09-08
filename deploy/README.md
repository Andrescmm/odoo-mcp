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

## Requisitos previos

- Un dominio, por ejemplo `mcp.fizz.software`, con DNS apuntando al servidor.
- Puertos TCP 80 y 443 accesibles para emitir y renovar el certificado.
- Odoo accesible por HTTPS desde Linux.
- Python 3.10+, `uv`, Caddy y permisos de administrador en Linux.
- Una clave Odoo `project_mcp` nueva; no reutilices una clave publicada en chats o historiales.

## 1. Instalar los archivos

Sube y descomprime este paquete en el servidor. Ejecuta los siguientes comandos desde la raíz descomprimida, donde se ven las carpetas `server`, `deploy` y `odoo_addons`.

Instala `uv` siguiendo su documentación oficial y verifica que exista `/usr/local/bin/uv`. Luego crea el usuario de servicio y copia el servidor:

```bash
sudo useradd --system --home-dir /opt/odoo-project-mcp --create-home \
  --shell /usr/sbin/nologin odoo-mcp
sudo install -d -o odoo-mcp -g odoo-mcp -m 750 /opt/odoo-project-mcp/server
sudo cp -a server/. /opt/odoo-project-mcp/server/
sudo chown -R odoo-mcp:odoo-mcp /opt/odoo-project-mcp/server
```

Instala las dependencias bloqueadas:

```bash
sudo -u odoo-mcp /usr/local/bin/uv sync \
  --directory /opt/odoo-project-mcp/server --frozen
```

## 2. Configurar secretos y red

```bash
sudo install -d -o root -g odoo-mcp -m 750 /etc/odoo-project-mcp
sudo install -d -o odoo-mcp -g odoo-mcp -m 700 /var/lib/odoo-project-mcp
sudo cp deploy/server.env.example /etc/odoo-project-mcp/server.env
sudo chown root:odoo-mcp /etc/odoo-project-mcp/server.env
sudo chmod 640 /etc/odoo-project-mcp/server.env
sudoedit /etc/odoo-project-mcp/server.env
```

Reemplaza `mcp.fizz.software`, la URL/base de Odoo, el login y la clave. Conserva `MCP_HOST=127.0.0.1`: el puerto 8000 no debe exponerse a Internet.

## 3. Crear los tres colaboradores

El comando muestra cada token una sola vez y guarda únicamente su SHA-256 en `users.json`.

```bash
sudo -u odoo-mcp /opt/odoo-project-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-project-mcp/users.json add andres@gmail.com \
  --name "Andres" --role admin --expires-days 180

sudo -u odoo-mcp /opt/odoo-project-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-project-mcp/users.json add desarrollador@gmail.com \
  --name "Desarrollador" --role developer --expires-days 180

sudo -u odoo-mcp /opt/odoo-project-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-project-mcp/users.json add revisor@gmail.com \
  --name "Revisor" --role reviewer --expires-days 180
```

Entrega cada token solo a su propietario mediante un canal seguro. Para rotar o revocar:

```bash
sudo -u odoo-mcp /opt/odoo-project-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-project-mcp/users.json rotate desarrollador@gmail.com

sudo -u odoo-mcp /opt/odoo-project-mcp/server/.venv/bin/odoo-project-mcp-users \
  --file /var/lib/odoo-project-mcp/users.json revoke desarrollador@gmail.com
```

Los cambios se recargan automáticamente; no requieren reiniciar el servicio.

Roles:

| Rol | Acceso |
|---|---|
| `reader` | Listar proyectos y leer tareas. |
| `developer` | Leer, comentar, iniciar y completar tareas. |
| `reviewer` | Leer, comentar y verificar tareas. |
| `admin` | Todas las operaciones anteriores. |

## 4. Instalar systemd

```bash
sudo cp deploy/systemd/odoo-project-mcp.service /etc/systemd/system/
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
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
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

Copia `deploy/nginx.conf.example` a `/etc/nginx/sites-available/mcp.fizz.software`, ajusta el dominio si hace falta y habilítalo. El archivo ya trae `proxy_buffering off` y timeouts largos: el transporte streamable-http deja la respuesta abierta y con el buffering por defecto el cliente parece colgado.

Primero el certificado. El bloque `:443` referencia archivos que todavía no existen, así que se emite antes de habilitar el sitio:

```bash
sudo apt install certbot
sudo certbot certonly --webroot -w /var/www/html -d mcp.fizz.software
```

Se usa `--webroot` y no `--nginx` a propósito: el plugin de nginx necesita un `server` que ya responda por el dominio, y todavía no lo hay. El sitio por defecto de nginx sirve `/var/www/html` en el puerto 80 para cualquier host, que es justo lo que el desafío ACME necesita.

Luego el sitio:

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/mcp.fizz.software
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

## Operación y seguridad

- Rota los tokens cada 180 días o inmediatamente si un equipo se pierde.
- Revisa `journalctl` y la vista **Project → Configuration → MCP API Audit** de Odoo.
- El MCP registra identificador, acción, estado e IP, pero nunca el token ni el cuerpo de la tarea.
- La identidad autenticada reemplaza el parámetro libre `agent` en modo HTTP para impedir suplantación.
- El rate limit es por colaborador y también limita intentos inválidos por IP.
- La limitación incluida funciona para un proceso. Si replicas el MCP, mueve rate limiting a nginx (`limit_req`), Redis o el balanceador.
- Tailscale es opcional: puede limitar el dominio a los tres dispositivos, pero conserva igualmente los tokens personales.
