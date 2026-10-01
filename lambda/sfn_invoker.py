"""
PRISMA — Lambda `invoker`: Step Functions -> POST al workflow (ALB interno).

Va DENTRO de la VPC (llega al ALB) y por eso es SOLO HTTP con la biblioteca estándar: sin boto3 y sin llamadas a APIs de AWS
(las subredes por defecto no dan internet a una Lambda en VPC). Todo lo que requiere AWS lo hace el workflow, que sí tiene salida.

Seguridad:
  * Lista blanca de fases: a, b, wait, finalize. No acepta rutas arbitrarias.
  * `session_id` debe ser un UUID (evita path traversal).
  * Solo reenvía las claves permitidas del evento (nunca texto libre).
  * No registra tokens de tarea, cuerpos ni el token interno.

Para `waitForTaskToken` el valor de retorno se ignora: la tarea termina cuando el workflow llama a SendTaskSuccess.
Variables: BACKEND_INTERNAL_URL, INTERNAL_TOKEN, API_TIMEOUT (30), RETRY_ATTEMPTS (3)
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

_FASES = {"a", "b", "wait", "finalize"}
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CLAVES_CUERPO = ("task_token", "attempt", "feedback_agent", "status")


def _post(url: str, cuerpo: dict) -> int:
    intentos = int(os.environ.get("RETRY_ATTEMPTS", "3"))
    espera_http = int(os.environ.get("API_TIMEOUT", "30"))
    datos = json.dumps(cuerpo).encode("utf-8")
    ultimo = None
    for intento in range(1, intentos + 1):
        req = urllib.request.Request(
            url, data=datos, method="POST",
            headers={"Content-Type": "application/json", "X-Internal-Token": os.environ["INTERNAL_TOKEN"]},
        )
        try:
            with urllib.request.urlopen(req, timeout=espera_http) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:          # 4xx: error nuestro, reintentar no lo arregla
                raise
            ultimo = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            ultimo = exc
        print(f"intento {intento}/{intentos} fallo: {type(ultimo).__name__}")
        if intento < intentos:
            time.sleep(2 ** intento)
    raise ultimo


def handler(event: dict, context) -> dict:
    fase = event.get("phase")
    session_id = event.get("session_id") or ""
    if fase not in _FASES:
        raise ValueError(f"fase no permitida: {fase!r}")
    if not _UUID.match(session_id):
        raise ValueError("session_id invalido")
    base = os.environ["BACKEND_INTERNAL_URL"].rstrip("/")
    cuerpo = {k: event[k] for k in _CLAVES_CUERPO if k in event}
    estado = _post(f"{base}/chat/internal/phase/{fase}/{session_id}", cuerpo)
    return {"http_status": estado}
