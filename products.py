@router.post("/seed")
def seed_products(db: Session = Depends(get_db)):

    products = [
        # 🔬 COLOIDES
        {"name": "Plata Coloidal", "price": 2},
        {"name": "Cobre Coloidal", "price": 2},
        {"name": "Selenio Coloidal", "price": 2},
        {"name": "Oro Coloidal", "price": 4},
        {"name": "Zinc Coloidal", "price": 3},
        {"name": "Magnesio Coloidal", "price": 3},
        {"name": "Silicio Coloidal", "price": 3},

        # 🌿 BASE
        {"name": "CBD 874 mg", "price": 6},
        {"name": "Melena de León", "price": 6},

        # 🍫 FUNCIONAL
        {"name": "Chocomedical", "price": 2},

        # 🍄 ADAPTÓGENOS
        {"name": "Ashwagandha", "price": 5},
        {"name": "Reishi", "price": 5},
        {"name": "Chaga", "price": 6},
    ]

    created = []

    for p in products:
        existing = db.query(models.Product).filter(models.Product.name == p["name"]).first()
        if not existing:
            product = models.Product(
                name=p["name"],
                price=p["price"]
            )
            db.add(product)
            created.append(product.name)

    db.commit()

    return {"message": "Productos creados correctamente", "products": created}
