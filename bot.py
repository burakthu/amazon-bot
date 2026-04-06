import requests
from bs4 import BeautifulSoup
import time
import json
import os
import re
from datetime import datetime

# ─────────────────────────────────────────────
#  AYARLAR — Railway'de Environment Variable
#  olarak girilecek, burayı değiştirmene gerek yok
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "BURAYA_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "BURAYA_CHAT_ID")

KONTROL_ARALIGI_SANIYE = 90   # 90 saniyede bir kontrol
URUNLER_DOSYASI        = "urunler.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8",
}

# ── ASIN çıkar ────────────────────────────────────────────────────────────────
def asin_cikart(url: str) -> str | None:
    """URL'den Amazon ASIN kodunu çıkarır. Örn: B0XXXXXXXX"""
    for pattern in [r"/dp/([A-Z0-9]{10})", r"/gp/product/([A-Z0-9]{10})"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None

def sepet_linki_olustur(asin: str) -> str:
    return f"https://www.amazon.com.tr/gp/aws/cart/add.html?ASIN={asin}&Quantity=1"

# ── Telegram: düz mesaj ───────────────────────────────────────────────────────
def telegram_gonder(mesaj: str) -> None:
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    veri = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mesaj,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, data=veri, timeout=10)
    except Exception as e:
        print(f"[HATA] Telegram mesajı: {e}")

# ── Telegram: "Sepete Ekle" + "Ürün Sayfası" butonlu mesaj ───────────────────
def telegram_butonlu_gonder(mesaj: str, sepet_url: str, urun_url: str) -> None:
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    klavye = {
        "inline_keyboard": [
            [{"text": "🛒  Sepete Ekle", "url": sepet_url}],
            [{"text": "📄  Ürün Sayfası", "url": urun_url}],
        ]
    }
    veri = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mesaj,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": json.dumps(klavye),
    }
    try:
        requests.post(url, data=veri, timeout=10)
    except Exception as e:
        print(f"[HATA] Telegram butonlu: {e}")

# ── Telegram: gelen komutları oku ─────────────────────────────────────────────
son_update_id = None

def komutlari_isle() -> None:
    global son_update_id
    url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 0, "offset": son_update_id}
    try:
        r    = requests.get(url, params=params, timeout=10).json()
        msgs = r.get("result", [])
        for m in msgs:
            son_update_id = m["update_id"] + 1
            metin = m.get("message", {}).get("text", "").strip()

            if metin.startswith("/start") or metin.startswith("/yardim"):
                yardim_gonder()
            elif metin.startswith("/ekle "):
                parcalar = metin.split(" ", 2)
                if len(parcalar) == 3:
                    try:
                        hedef = float(parcalar[2].replace(",", "."))
                        urun_ekle(parcalar[1], hedef)
                    except ValueError:
                        telegram_gonder(
                            "❌ Kullanım: /ekle [URL] [hedef fiyat]\n"
                            "Örnek: /ekle https://amazon.com.tr/dp/B0XX 1500"
                        )
                else:
                    telegram_gonder(
                        "❌ Kullanım: /ekle [URL] [hedef fiyat]\n"
                        "Örnek: /ekle https://amazon.com.tr/dp/B0XX 1500"
                    )
            elif metin.startswith("/liste"):
                urunleri_listele_telegram()
            elif metin.startswith("/sil "):
                parcalar = metin.split(" ", 1)
                try:
                    urun_sil(int(parcalar[1]))
                except ValueError:
                    telegram_gonder("❌ Kullanım: /sil [numara]\nÖrnek: /sil 2")
            elif metin.startswith("/kontrol"):
                fiyatlari_kontrol_et(sessiz=False)
    except Exception as e:
        print(f"[HATA] Komut okuma: {e}")

def yardim_gonder() -> None:
    telegram_gonder(
        "🤖 <b>Amazon TR Fiyat &amp; Stok Takip Botu</b>\n\n"
        "<b>Komutlar:</b>\n\n"
        "/ekle [URL] [hedef fiyat]\n"
        "   Ürün takibe al\n"
        "   Örnek: /ekle https://amazon.com.tr/dp/B0XX 1500\n\n"
        "/liste — Takip listeni göster\n"
        "/sil [no] — Listeden ürün çıkar\n"
        "/kontrol — Şimdi kontrol et\n"
        "/yardim — Bu menüyü göster\n\n"
        "⚡ Koşul gerçekleşince sana <b>🛒 Sepete Ekle</b> butonu gelecek!"
    )

# ── Amazon'dan veri çek ───────────────────────────────────────────────────────
def sayfa_cek(url: str) -> dict:
    sonuc = {"ad": "Bilinmiyor", "fiyat_str": None, "fiyat_num": None, "stokta": None}
    try:
        r    = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.content, "html.parser")

        # Ürün adı
        baslik = soup.find(id="productTitle")
        if baslik:
            sonuc["ad"] = baslik.get_text(strip=True)

        # Fiyat
        for sec in [
            "span.a-price span.a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            ".a-price .a-offscreen",
        ]:
            elem = soup.select_one(sec)
            if elem:
                sonuc["fiyat_str"] = elem.get_text(strip=True)
                sonuc["fiyat_num"] = fiyat_parse(sonuc["fiyat_str"])
                break

        # Stok durumu
        stok_elem = soup.find(id="availability")
        if stok_elem:
            stok_metni = stok_elem.get_text(strip=True).lower()
            if any(k in stok_metni for k in ["stokta yok", "geçici", "out of stock", "unavailable"]):
                sonuc["stokta"] = False
            elif any(k in stok_metni for k in ["stokta", "sepete ekle", "in stock"]):
                sonuc["stokta"] = True

        # Sepete ekle butonu varsa kesinlikle stokta var
        if sonuc["stokta"] is None and soup.find(id="add-to-cart-button"):
            sonuc["stokta"] = True

    except Exception as e:
        print(f"[HATA] Sayfa: {e}")
    return sonuc

def fiyat_parse(fiyat_str: str) -> float | None:
    try:
        temiz = fiyat_str.replace("TL", "").replace("₺", "").strip()
        return float(temiz.replace(".", "").replace(",", "."))
    except Exception:
        return None

# ── Ürün listesi yönetimi ─────────────────────────────────────────────────────
def urunleri_yukle() -> list:
    if os.path.exists(URUNLER_DOSYASI):
        with open(URUNLER_DOSYASI, encoding="utf-8") as f:
            return json.load(f)
    return []

def urunleri_kaydet(urunler: list) -> None:
    with open(URUNLER_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(urunler, f, ensure_ascii=False, indent=2)

def urun_ekle(url: str, hedef_fiyat: float) -> None:
    urunler = urunleri_yukle()
    telegram_gonder("⏳ Ürün bilgisi alınıyor...")
    veri = sayfa_cek(url)
    asin = asin_cikart(url)

    if not asin:
        telegram_gonder("❌ ASIN kodu bulunamadı. Geçerli bir Amazon ürün URL'si gir.")
        return

    stok_emoji = "✅" if veri["stokta"] else ("❌" if veri["stokta"] is False else "❓")
    stok_yazi  = "Stokta var" if veri["stokta"] else ("Stokta yok" if veri["stokta"] is False else "Bilinmiyor")

    urun = {
        "url":         url,
        "asin":        asin,
        "ad":          veri["ad"],
        "hedef_fiyat": hedef_fiyat,
        "son_fiyat":   veri["fiyat_str"],
        "son_stok":    veri["stokta"],
        "eklendi":     datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    urunler.append(urun)
    urunleri_kaydet(urunler)

    telegram_gonder(
        f"✅ <b>Ürün eklendi!</b>\n\n"
        f"📦 {veri['ad'][:70]}\n"
        f"💰 Şu anki fiyat: {veri['fiyat_str'] or 'Alınamadı'}\n"
        f"{stok_emoji} Stok: {stok_yazi}\n"
        f"🎯 Hedef fiyat: {hedef_fiyat:.0f} TL\n\n"
        f"Her {KONTROL_ARALIGI_SANIYE} saniyede bir kontrol edeceğim.\n"
        f"Koşul gerçekleşince 🛒 <b>Sepete Ekle</b> butonu göndereceğim!"
    )
    print(f"[+] Eklendi: {veri['ad'][:50]} | ASIN: {asin}")

def urun_sil(no: int) -> None:
    urunler = urunleri_yukle()
    if no < 1 or no > len(urunler):
        telegram_gonder(f"❌ Geçersiz numara. Listede {len(urunler)} ürün var.")
        return
    silinen = urunler.pop(no - 1)
    urunleri_kaydet(urunler)
    telegram_gonder(f"🗑️ Silindi: {silinen['ad'][:60]}")

def urunleri_listele_telegram() -> None:
    urunler = urunleri_yukle()
    if not urunler:
        telegram_gonder("📭 Takip listesi boş.\n\n/ekle komutuyla ürün ekleyebilirsin.")
        return
    mesaj = "📋 <b>Takip Listesi</b>\n\n"
    for i, u in enumerate(urunler, 1):
        stok_emoji = "✅" if u["son_stok"] else ("❌" if u["son_stok"] is False else "❓")
        mesaj += (
            f"{i}. {u['ad'][:50]}\n"
            f"   💰 {u['son_fiyat'] or '?'}  →  🎯 Hedef: {u['hedef_fiyat']} TL\n"
            f"   {stok_emoji} Stok  |  📅 {u['eklendi']}\n\n"
        )
    mesaj += "Silmek için: /sil [numara]"
    telegram_gonder(mesaj)

# ── Ana kontrol döngüsü ───────────────────────────────────────────────────────
def fiyatlari_kontrol_et(sessiz: bool = True) -> None:
    urunler = urunleri_yukle()
    if not urunler:
        if not sessiz:
            telegram_gonder("📭 Takip listesi boş.")
        return

    zaman = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{zaman}] {len(urunler)} ürün kontrol ediliyor...")
    degisiklik = False

    for urun in urunler:
        veri      = sayfa_cek(urun["url"])
        asin      = urun.get("asin") or asin_cikart(urun["url"])
        sepet_url = sepet_linki_olustur(asin) if asin else urun["url"]

        yeni_fiyat  = veri["fiyat_str"]
        yeni_stok   = veri["stokta"]
        fiyat_num   = veri["fiyat_num"]
        hedef       = float(urun["hedef_fiyat"])
        eski_fiyat  = fiyat_parse(urun["son_fiyat"]) if urun["son_fiyat"] else None
        eski_stok   = urun["son_stok"]

        stok_yazi = "✅ Stokta var" if yeni_stok else ("❌ Stokta yok" if yeni_stok is False else "❓")
        print(f"  {urun['ad'][:40]} → {yeni_fiyat} | {stok_yazi}")

        # ── Koşul 1: Hedef fiyata ulaşıldı ──────────────────────────────────
        if fiyat_num and fiyat_num <= hedef:
            telegram_butonlu_gonder(
                f"🚨 <b>HEDEF FİYATA ULAŞILDI!</b>\n\n"
                f"📦 {urun['ad'][:70]}\n"
                f"💰 Fiyat: <b>{yeni_fiyat}</b>\n"
                f"🎯 Hedefin: {hedef:.0f} TL\n"
                f"{stok_yazi}",
                sepet_url, urun["url"]
            )

        # ── Koşul 2: Stoka girdi (önceden yoktu) ─────────────────────────────
        elif yeni_stok is True and eski_stok is False:
            telegram_butonlu_gonder(
                f"📦 <b>ÜRÜN STOKA GİRDİ!</b>\n\n"
                f"📦 {urun['ad'][:70]}\n"
                f"💰 Fiyat: {yeni_fiyat or '?'}\n"
                f"🎯 Hedef fiyatın: {hedef:.0f} TL",
                sepet_url, urun["url"]
            )

        # ── Koşul 3: Fiyat düştü ama henüz hedefe ulaşmadı ──────────────────
        elif (yeni_fiyat != urun["son_fiyat"]
              and fiyat_num and eski_fiyat
              and fiyat_num < eski_fiyat
              and fiyat_num > hedef):
            telegram_butonlu_gonder(
                f"📉 <b>Fiyat Düştü</b>\n\n"
                f"📦 {urun['ad'][:70]}\n"
                f"💰 {urun['son_fiyat']} → <b>{yeni_fiyat}</b>\n"
                f"🎯 Hedefine {fiyat_num - hedef:.0f} TL kaldı",
                sepet_url, urun["url"]
            )

        # Değişiklikleri kaydet
        if yeni_fiyat != urun["son_fiyat"] or yeni_stok != eski_stok:
            urun["son_fiyat"] = yeni_fiyat
            urun["son_stok"]  = yeni_stok
            degisiklik = True

    if degisiklik:
        urunleri_kaydet(urunler)

    if not sessiz:
        telegram_gonder(f"✅ {len(urunler)} ürün kontrol edildi. ({zaman})")

# ── Başlangıç ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  Amazon TR Fiyat & Stok Takip Botu")
    print(f"  Kontrol aralığı: {KONTROL_ARALIGI_SANIYE} saniye")
    print("=" * 50)

    telegram_gonder(
        f"🤖 <b>Bot başlatıldı!</b>\n"
        f"Her {KONTROL_ARALIGI_SANIYE} saniyede bir kontrol edeceğim.\n\n"
        f"/yardim yazarak komutları görebilirsin."
    )

    while True:
        try:
            komutlari_isle()
            fiyatlari_kontrol_et(sessiz=True)
            time.sleep(KONTROL_ARALIGI_SANIYE)
        except KeyboardInterrupt:
            print("\n⏹️  Bot durduruldu.")
            telegram_gonder("⏹️ Bot durduruldu.")
            break
        except Exception as e:
            print(f"[HATA] Ana döngü: {e}")
            time.sleep(30)
