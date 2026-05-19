from typing import Optional

from pydantic import BaseModel, Field


class StartChatResponse(BaseModel):
    session_id: str = Field(..., description="UUID único de la sesión creada")


class SessionStateResponse(BaseModel):
    phase: str = Field(
        ...,
        description="Estado actual de la sesión: running | awaiting_hitl | completed | error",
    )
    messages: list[dict] = Field(
        default_factory=list,
        description="Historial de mensajes generados durante el flujo",
    )
    hitl_data: Optional[dict] = Field(
        None,
        description="Datos del checkpoint HITL activo (plan de adaptación a revisar), si aplica",
    )
    error: Optional[str] = Field(
        None,
        description="Mensaje de error legible si phase=='error'",
    )
    workflow_status: Optional[str] = Field(
        None,
        description="Resultado final del flujo: success | degraded | hitl_rejected | error | cancelled",
    )


class DownloadResponse(BaseModel):
    url: str = Field(
        ...,
        description="URL de descarga del DOCX generado. Presigned URL de S3 en modo AWS, ruta local en dev",
    )
    filename: str = Field(..., description="Nombre del archivo DOCX generado")
    expires_in: int = Field(
        ...,
        description="Segundos hasta que expire el presigned URL (relevante solo en modo AWS)",
    )


class OkResponse(BaseModel):
    ok: bool = Field(..., description="True si la operación fue aceptada")


class InternalRunResponse(BaseModel):
    started: bool = Field(..., description="True si el workflow fue encolado exitosamente")


class HitlResponseBody(BaseModel):
    approved: bool = Field(..., description="True si el docente aprueba el plan de adaptación")
    reason: Optional[str] = Field(
        None,
        description="Motivo del rechazo o comentario para el agente",
    )
    agent_to_retry: Optional[int] = Field(
        None,
        description="Agente a reintentar en caso de rechazo: 1=AnalizadorPACI, 2=Adaptador",
    )
