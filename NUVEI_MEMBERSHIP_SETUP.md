# Nuvei recurrente — Mayu Wellness Club

Esta integración aplica únicamente a las membresías de Mayu Wellness Club. No
modifica los pagos de Farmacia, Educación ni Mayu Medic.

## Variables en Render

Configurar como secretos; no guardarlas en Git:

```text
NUVEI_MODE=sandbox
NUVEI_CLIENT_APP_CODE=
NUVEI_CLIENT_APP_KEY=
NUVEI_SERVER_APP_CODE=
NUVEI_SERVER_APP_KEY=
NUVEI_TOKEN_ENCRYPTION_KEY=
NUVEI_CALLBACK_URL=https://mayu-wellness-backend-v1.onrender.com/payments/nuvei/membership/webhook
PUBLIC_API_BASE_URL=https://mayu-wellness-backend-v1.onrender.com
SECURITY_ALERT_EMAIL=jvdirectorgeneral@mayuecuador.com
NUVEI_VAT_PERCENTAGE=15
NUVEI_CRON_SECRET=
NUVEI_MAX_RETRY_ATTEMPTS=3
NUVEI_SANDBOX_RENEWAL_INTERVAL_DAYS=2
```

`NUVEI_SANDBOX_RENEWAL_INTERVAL_DAYS` acelera las renovaciones para pruebas.
Solo funciona con `NUVEI_MODE=sandbox`; se ignora automáticamente en `live` o
`production`, donde la renovación conserva su periodicidad mensual.

El `CLIENT_APP_KEY` se entrega solamente al socio autenticado para ejecutar el
SDK de tokenización. El `SERVER_APP_KEY` nunca sale del backend. MAYU nunca
recibe el PAN. El CVC se transmite cifrado por HTTPS únicamente para el primer
débito, se elimina de memoria después de la llamada y jamás se guarda ni se
registra. PostgreSQL conserva el token y metadatos no sensibles.

`SECRET_KEY` es obligatoria en Render, debe ser aleatoria, exclusiva de este
servicio y tener al menos 48 caracteres. Nunca debe guardarse en Git.
`NUVEI_TOKEN_ENCRYPTION_KEY` debe ser una clave Fernet diferente de
`SECRET_KEY`; cifra los tokens de tarjeta antes de guardarlos en PostgreSQL.

El primer débito usa 3DS y puede devolver `status_detail` 35 (frictionless) o
36 (challenge). El cliente debe abrir el `content_url` devuelto y, para 35,
esperar cinco segundos antes de llamar `continue_url`. Las renovaciones usan
solamente el token y no reenvían CVC ni `threeDS2_data`.

Los incidentes de firma, monto, callback desconocido, tres rechazos o error 3DS
se guardan en `security_events` y se notifican sin secretos al correo definido
en `SECURITY_ALERT_EMAIL`.

## Cron diario

Crear un Cron Job en Render con el mismo repositorio y variables del backend:

- Prueba acelerada: `0 * * * *` (revisión cada hora; solo cobra al vencer).
- Producción mensual: `0 5 * * *` (medianoche de Ecuador continental).
- Comando: `python scripts/run_nuvei_membership_cron.py`

El cron consulta tarjetas activas, intenta el débito cuando corresponde y
aplica reintentos diarios hasta el límite configurado. El callback reconcilia
la transacción de forma idempotente y solo aprueba `status=success` junto con
`status_detail=3`.

## Certificación

Mantener `NUVEI_MODE=sandbox` durante desarrollo y pruebas. Las credenciales de
producción deben configurarse únicamente después de la certificación formal de
Nuvei.
