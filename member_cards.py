@router.get("/user/{user_id}/image")
def generate_card_image(user_id: int, db: Session = Depends(get_db)):
    from PIL import ImageFont

    card = db.query(models.MemberCard).filter(
        models.MemberCard.user_id == user_id
    ).first()

    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    width, height = 950, 560
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # =========================
    # FONDO POR NIVEL
    # =========================
    if card.level_snapshot == 1:
        level_text = "Nivel 1 - Cobre"
        bg_path = os.path.join(base_dir, "assets", "card_cobre.jpg")
        accent_color = (236, 210, 190)
        text_color = (20, 15, 12)
    elif card.level_snapshot == 2:
        level_text = "Nivel 2 - Plata"
        bg_path = os.path.join(base_dir, "assets", "card_plata.jpg")
        accent_color = (245, 245, 245)
        text_color = (20, 20, 20)
    else:
        level_text = "Nivel 3 - Oro"
        bg_path = os.path.join(base_dir, "assets", "card_oro.jpg")
        accent_color = (255, 236, 170)
        text_color = (35, 28, 10)

    if os.path.exists(bg_path):
        image = Image.open(bg_path).convert("RGB")
        image = image.resize((width, height))
    else:
        image = Image.new("RGB", (width, height), (220, 220, 220))

    draw = ImageDraw.Draw(image)

    # =========================
    # FUENTES GRANDES
    # =========================
    try:
        font_title = ImageFont.truetype("arial.ttf", 48)
        font_name = ImageFont.truetype("arial.ttf", 38)
        font_text = ImageFont.truetype("arial.ttf", 30)
    except:
        font_title = ImageFont.load_default()
        font_name = ImageFont.load_default()
        font_text = ImageFont.load_default()

    # =========================
    # MARCO
    # =========================
    draw.rounded_rectangle(
        [(18, 18), (width - 18, height - 18)],
        radius=30,
        outline=accent_color,
        width=4
    )

    # =========================
    # LOGO CENTRADO GRANDE
    # =========================
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        logo = logo.resize((160, 160))  # MÁS GRANDE

        logo_x = (width - 160) // 2
        logo_y = 30

        image.paste(logo, (logo_x, logo_y), logo)

    # =========================
    # TÍTULO GRANDE
    # =========================
    title = "MAYU WELLNESS CLUB"

    bbox = draw.textbbox((0, 0), title, font=font_title)
    text_width = bbox[2] - bbox[0]
    title_x = (width - text_width) // 2

    draw.text(
        (title_x, 200),
        title,
        fill=text_color,
        font=font_title
    )

    # =========================
    # DATOS (5X MÁS GRANDES)
    # =========================
    draw.text((60, 280), user.name, fill=text_color, font=font_name)
    draw.text((60, 330), level_text, fill=text_color, font=font_text)
    draw.text((60, 380), f"Código: {card.member_code}", fill=text_color, font=font_text)
    draw.text((60, 420), f"Válido hasta: {card.expires_at}", fill=text_color, font=font_text)
    draw.text((60, 460), f"Estado: {card.status}", fill=text_color, font=font_text)

    # =========================
    # QR
    # =========================
    qr = qrcode.make(card.qr_token)
    qr = qr.resize((170, 170))
    image.paste(qr, (730, 350))

    # =========================
    # GUARDAR
    # =========================
    file_path = f"card_{user_id}.png"
    image.save(file_path)

    return FileResponse(
        file_path,
        media_type="image/png",
        filename=f"mayu_card_{user_id}.png"
    )
