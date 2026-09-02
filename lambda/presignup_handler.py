"""
PRISMA — Cognito Pre sign-up trigger: allowlist de correos.

Corre justo antes de que Cognito cree un usuario (incluidos los federados de
Google, triggerSource "PreSignUp_ExternalProvider"). Si el correo no esta en
la allowlist, lanza una excepcion y el usuario NUNCA se crea; la Hosted UI
muestra el mensaje de la excepcion en la pantalla de login.

Solo filtra la CREACION: un usuario que ya existe en el pool no pasa por aca.
Para revocar a alguien ya creado hay que borrarlo desde la consola de Cognito.

Trigger configuration:
  User Pool -> Extensions -> Add Lambda trigger -> Sign-up -> Pre sign-up
  Runtime  : Python 3.12
  Memory   : 128 MB
  Timeout  : 5 seconds (sin red, sin VPC: pura comparacion de strings)

Required environment variables:
  ALLOWED_EMAILS — correos permitidos, separados por coma (case-insensitive)
"""

import os

ALLOWED_EMAILS = {
    email.strip().lower()
    for email in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if email.strip()
}

# El alta manual desde la consola/API de Cognito es una accion de un admin
# autenticado en AWS: no se filtra por allowlist.
ADMIN_TRIGGER = "PreSignUp_AdminCreateUser"


def handler(event: dict, context) -> dict:
    if event.get("triggerSource") == ADMIN_TRIGGER:
        return event

    email = (
        event.get("request", {}).get("userAttributes", {}).get("email", "")
    ).strip().lower()

    if not ALLOWED_EMAILS:
        # Sin allowlist configurada preferimos cerrar todo antes que abrir todo.
        raise Exception("Registro deshabilitado: ALLOWED_EMAILS no esta configurada.")

    if email not in ALLOWED_EMAILS:
        print(f"Rechazado intento de registro: {email!r}")
        raise Exception(
            "Esta cuenta no esta autorizada para acceder a P.R.I.S.M.A. "
            "Contacta al administrador del sistema."
        )

    print(f"Registro permitido: {email!r}")
    return event
