import re
from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

import models


def normalize_email(value: Optional[str]) -> Optional[str]:
    cleaned = (value or "").strip().lower()
    return cleaned or None


def normalize_phone(value: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return None
    if digits.startswith("0") and len(digits) == 10:
        digits = "593" + digits[1:]
    return digits


def _merge_csv(current: Optional[str], value: Optional[str]) -> str:
    values = {item.strip() for item in (current or "").split(",") if item.strip()}
    values.update(item.strip() for item in (value or "").split(",") if item.strip())
    return ",".join(sorted(values))


def upsert_marketing_contact(db: Session, *, name: Optional[str], email: Optional[str] = None,
    phone: Optional[str] = None, source: str, user_id: Optional[int] = None,
    doctor_prescriber_id: Optional[int] = None, tags: Optional[str] = None,
    marketing_consent: Optional[bool] = None, consent_source: Optional[str] = None,
    **_ignored):
    normalized_email = normalize_email(email)
    normalized_phone = normalize_phone(phone)
    filters = []
    if normalized_email:
        filters.append(models.MarketingContact.normalized_email == normalized_email)
    if normalized_phone:
        filters.append(models.MarketingContact.normalized_phone == normalized_phone)
    if user_id:
        filters.append(models.MarketingContact.user_id == user_id)
    if doctor_prescriber_id:
        filters.append(models.MarketingContact.doctor_prescriber_id == doctor_prescriber_id)
    contact = db.query(models.MarketingContact).filter(or_(*filters)).first() if filters else None
    now = datetime.utcnow()
    if contact is None:
        contact = models.MarketingContact(name=(name or "Contacto Mayu").strip(),
            email=normalized_email, phone=(phone or "").strip() or None,
            normalized_email=normalized_email, normalized_phone=normalized_phone,
            sources=source, marketing_consent=bool(marketing_consent),
            consent_source=consent_source if marketing_consent else None,
            consent_at=now if marketing_consent else None)
        db.add(contact)
    else:
        if name and name.strip(): contact.name = name.strip()
        contact.email = normalized_email or contact.email
        contact.phone = (phone or "").strip() or contact.phone
        contact.normalized_email = normalized_email or contact.normalized_email
        contact.normalized_phone = normalized_phone or contact.normalized_phone
        contact.sources = _merge_csv(contact.sources, source)
        if marketing_consent is True:
            contact.marketing_consent = True
            contact.unsubscribed_at = None
            contact.consent_source = consent_source or source
            contact.consent_at = contact.consent_at or now
    contact.user_id = user_id or contact.user_id
    contact.doctor_prescriber_id = doctor_prescriber_id or contact.doctor_prescriber_id
    contact.tags = _merge_csv(contact.tags, tags)
    db.flush()
    return contact


def contact_to_dict(contact):
    return {"id": contact.id, "user_id": contact.user_id,
        "doctor_prescriber_id": contact.doctor_prescriber_id, "name": contact.name,
        "email": contact.email, "phone": contact.phone,
        "sources": [x for x in (contact.sources or "").split(",") if x],
        "tags": [x for x in (contact.tags or "").split(",") if x],
        "marketing_consent": contact.marketing_consent, "consent_source": contact.consent_source,
        "consent_at": contact.consent_at, "unsubscribed_at": contact.unsubscribed_at,
        "created_at": contact.created_at, "updated_at": contact.updated_at}
