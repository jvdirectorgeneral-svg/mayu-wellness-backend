from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal
from models import User, Ambassador, Commission

router = APIRouter(prefix="/admin-dashboard", tags=["admin-dashboard"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/summary")
def get_admin_summary(db: Session = Depends(get_db)):
    total_users = db.query(User).count()

    active_users = db.query(User).filter(
        User.membership_active == True
    ).count()

    inactive_users = db.query(User).filter(
        User.membership_active == False
    ).count()

    total_ambassadors = db.query(Ambassador).count()

    total_generated = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).scalar()

    total_pending = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(Commission.status == "pending").scalar()

    total_paid = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(Commission.status == "paid").scalar()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": inactive_users,
        "total_ambassadors": total_ambassadors,
        "total_generated": float(total_generated or 0),
        "total_pending": float(total_pending or 0),
        "total_paid": float(total_paid or 0)
    }


@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id.desc()).all()

    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "membership_level": u.membership_level,
            "membership_active": u.membership_active,
            "role": u.role
        }
        for u in users
    ]


@router.get("/ambassadors")
def get_ambassadors(db: Session = Depends(get_db)):
    ambassadors = db.query(Ambassador).order_by(Ambassador.id.desc()).all()

    return [
        {
            "id": a.id,
            "user_id": a.user_id,
            "code": a.ambassador_code,
            "status": a.status,
            "is_active": a.is_active
        }
        for a in ambassadors
    ]


@router.get("/commissions/pending")
def get_pending_commissions(db: Session = Depends(get_db)):
    commissions = db.query(Commission).filter(
        Commission.status == "pending"
    ).all()

    return [
        {
            "id": c.id,
            "ambassador_id": c.ambassador_id,
            "amount": c.commission_amount,
            "month": c.month,
            "year": c.year
        }
        for c in commissions
    ]
