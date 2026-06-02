import qrcode
from PIL import Image

def fabriquer_qr_lune_souverain():
    qr = qrcode.QRCode(
        version=5,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    url_cible = "https://nommora.com"
    qr.add_data(url_cible)
    qr.make(fit=True)
    image_qr = qr.make_image(fill_color="#C8A44E", back_color="#0B0F17").convert('RGB')
    chemin_sortie = "/Users/pro/Desktop/Nommora_MoonGate_QR.png"
    image_qr.save(chemin_sortie)
    print(f"✅ Déclencheur Moon Gate généré sur le Bureau : {chemin_sortie}")

if __name__ == "__main__":
    fabriquer_qr_lune_souverain()
