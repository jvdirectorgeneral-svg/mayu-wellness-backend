import json
import os
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from marketing import get_admin_notification_emails, normalize_admin_email_list, send_marketing_email
from models import SecurityEvent


SENSITIVE_KEYS = {
    "authorization",
    "auth-token",
    "app_key",
    "client_app_key",
    "cvc",
    "cvv",
    "password",
    "secret",
    "stoken",
    "token",
}


def _sanitize(value: Any, key: str = "") -> Any:
    if key.lower() in SENSITIVE_KEYS or any(part in key.lower() for part in ("secret", "password", "token", "cvc", "cvv")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    text = str(value) if value is not None else None
    return text[:500] if text else text


def record_security_event(
    db: Session,
    *,
    event_type: str,
    summary: str,
    severity: str = "warning",
    source: str = "application",
    reference: Optional[str] = None,
    metadata: Optional[dict] = None,
    notify: bool = True,
) -> SecurityEvent:
    safe_metadata = _sanitize(metadata or {})
    event = SecurityEvent(
        event_type=event_type[:120],
        severity=severity[:32],
        source=source[:120],
        reference=(reference or "")[:255] or None,
        summary=summary[:2000],
        metadata_json=json.dumps(safe_metadata, ensure_ascii=False, sort_keys=True),
    )
    db.add(event)
    db.flush()

    if notify:
        message = "\n".join([
            "Alerta automática de seguridad Mayu Wellness Club",
            "",
            f"Evento: {event.event_type}",
            f"Severidad: {event.severity}",
            f"Origen: {event.source}",
            f"Referencia: {event.reference or '-'}",
            f"Fecha UTC: {datetime.utcnow().isoformat()}Z",
            f"Resumen: {event.summary}",
            "",
            "Los datos sensibles fueron ocultados automáticamente.",
        ])
        try:
            configured = normalize_admin_email_list(
                os.getenv("SECURITY_ALERT_EMAIL", "jvdirectorgeneral@mayuecuador.com")
            )
            recipients = list(dict.fromkeys(configured + get_admin_notification_emails(db)))
            errors = []
            for recipient in recipients:
                try:
                    send_marketing_email(
                        to_email=recipient,
                        subject=f"[SEGURIDAD {event.severity.upper()}] {event.event_type}",
                        message=message,
                    )
                    event.notified = True
                except Exception as exc:
                    errors.append(f"{recipient}: {exc}")
            if errors:
                event.notification_error = "; ".join(errors)[:2000]
        except Exception as exc:
            event.notification_error = str(exc)[:2000]
    return event
