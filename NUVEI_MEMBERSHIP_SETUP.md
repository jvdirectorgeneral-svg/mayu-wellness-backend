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
NUVEI_CALLBACK_URL=https://mayu-wellness-backend-v1.onrender.com/payments/nuvei/membership/webhook
NUVEI_CRON_SECRET=
NUVEI_MAX_RETRY_ATTEMPTS=3
```

El `CLIENT_APP_KEY` se entrega solamente al socio autenticado para ejecutar el
SDK de tokenización. El `SERVER_APP_KEY` nunca sale del backend. MAYU no recibe
ni almacena PAN o CVV; persiste únicamente el token y metadatos no sensibles.

## Cron diario

Crear un Cron Job en Render con el mismo repositorio y variables del backend:

- Horario: `0 5 * * *` (medianoche de Ecuador continental).
- Comando: `python scripts/run_nuvei_membership_cron.py`

El cron consulta tarjetas activas, intenta el débito cuando corresponde y
aplica reintentos diarios hasta el límite configurado. El callback reconcilia
la transacción de forma idempotente y solo aprueba `status=success` junto con
`status_detail=3`.

## Certificación

Mantener `NUVEI_MODE=sandbox` durante desarrollo y pruebas. Las credenciales de
producción deben configurarse únicamente después de la certificación formal de
Nuvei.
