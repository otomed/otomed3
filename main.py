# -*- coding: utf-8 -*-
# Otomed.ai - Mastodon & DeepSeek AI Bot (Kararlı Sürüm)

import os
import time
import requests
from dotenv import load_dotenv
from mastodon import Mastodon

# --- Konfigürasyon ve Ortam Değişkenleri ---

# .env dosyasındaki değişkenleri yükleyerek kodun dışından güvenli yönetim sağlar.
load_dotenv()

# Hyperbolic ve Mastodon için gerekli tüm ayarlar .env dosyasından alınır.
HYPERBOLIC_API_KEY = os.getenv("HYPERBOLIC_API_KEY")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")
MASTODON_API_BASE_URL = os.getenv("MASTODON_API_BASE_URL")

# Hyperbolic API için kullanılacak model ve URL.
# Model adı: Sağlayıcının dokümantasyonuna göre 'deepseek-ai/DeepSeek-V3' olarak güncellendi.
HYPERBOLIC_MODEL = "deepseek-ai/DeepSeek-V3"
HYPERBOLIC_API_URL = "https://api.hyperbolic.xyz/v1/chat/completions"

# Son işlenen mention ID'sinin saklanacağı dosya.
LAST_ID_FILE = "last_mention_id.txt"

# --- Yardımcı Fonksiyonlar ---

def get_last_mention_id():
    """Son işlenen mention ID'sini dosyadan okur. Dosya yoksa None döner."""
    if os.path.exists(LAST_ID_FILE):
        with open(LAST_ID_FILE, "r") as f:
            content = f.read().strip()
            return content if content.isdigit() else None
    return None

def save_last_mention_id(mention_id):
    """Son işlenen mention ID'sini dosyaya güvenli bir şekilde yazar."""
    with open(LAST_ID_FILE, "w") as f:
        f.write(str(mention_id))

# --- Çekirdek Fonksiyonlar ---

def generate_response(prompt: str) -> str | None:
    """
    Hyperbolic API'ye istek göndererek yapay zeka yanıtı üretir.
    Geçici sunucu hatalarında (5xx) veya ağ sorunlarında akıllı yeniden deneme yapar.
    """
    retries = 3  # Toplam deneme hakkı
    delay = 5    # Denemeler arası bekleme süresi (saniye)

    for attempt in range(retries):
        print(f"'{prompt[:30]}...' için yapay zeka yanıtı üretiliyor... (Deneme {attempt + 1})")
        try:
            response = requests.post(
                HYPERBOLIC_API_URL,
                headers={
                    "Authorization": f"Bearer {HYPERBOLIC_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": HYPERBOLIC_MODEL,
                    "messages": [
                        {"role": "system", "content": "Sen Teknofest sosyal platformunda çalışan, kullanıcılara samimi, net ve destekleyici cevaplar veren bir yapay zeka asistanısın."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 1000, # Yanıt uzunluğunu biraz artırdık.
                    "temperature": 0.75,
                },
                timeout=30 # Sunucu yanıt süresi için daha toleranslı bir timeout.
            )

            # 5xx HTTP kodları sunucu taraflı geçici hatalardır. Bu durumda yeniden deneriz.
            if 500 <= response.status_code < 600:
                print(f"-> Sunucu Hatası ({response.status_code})! {delay} saniye sonra yeniden denenecek.")
                time.sleep(delay)
                delay *= 2  # Bir sonraki denemede daha uzun bekle (Exponential Backoff)
                continue

            # 4xx HTTP kodları genellikle kalıcı hatalardır (örn: yanlış API anahtarı).
            if response.status_code != 200:
                print(f"-> Kalıcı API Hatası: {response.status_code}, Yanıt: {response.text}")
                return None

            # Yanıt başarılıysa, JSON verisini işle.
            data = response.json()
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"].strip()
                print("-> Yanıt başarıyla üretildi.")
                return content
            else:
                print(f"-> Yanıtta beklenmeyen format: {data}")
                return None

        except requests.exceptions.RequestException as e:
            print(f"-> Ağ Hatası: {e}. {delay} saniye sonra yeniden denenecek.")
            time.sleep(delay)
            delay *= 2
    
    print("-> Tüm yeniden denemeler başarısız oldu. Model erişimi şu anda mümkün değil.")
    return None


def process_mentions(mastodon, bot_username: str):
    """
    Mastodon bildirimlerini işler, mention'lara yanıt verir.
    """
    last_id = get_last_mention_id()
    
    # since_id, None veya geçerli bir ID olmalıdır.
    notifications = mastodon.notifications(since_id=last_id if last_id else None)
    
    if not notifications:
        return # İşlenecek yeni bildirim yok.

    print(f"{len(notifications)} yeni bildirim bulundu. İşleniyor...")
    
    # Bildirimleri en eskiden en yeniye doğru işlemek, yanıtların sırasını korur.
    for notification in reversed(notifications):
        if notification["type"] == "mention":
            status = notification["status"]
            author_acct = notification["account"]["acct"]

            # Botun kendi kendine veya başkasına yanıt vermesini engelle
            if author_acct == bot_username:
                continue

            # Sadece bota doğrudan yapılan mention'ları işle
            bot_mention_string = f"@{bot_username}"
            # HTML etiketlerini temizleyerek daha güvenilir bir kontrol sağlıyoruz.
            plain_content = requests.utils.unquote(status['content']).replace('<p>', '').replace('</p>', ' ')
            if bot_mention_string not in plain_content:
                continue

            # Yanıt metnini oluştur
            prompt = plain_content.replace(bot_mention_string, "").strip()
            
            # Eğer prompt boşsa (sadece etiket atılmışsa)
            if not prompt:
                response_text = "Merhaba! Bana nasıl yardımcı olabileceğimi söyler misin?"
            else:
                response_text = generate_response(prompt)
            
            # Yapay zeka yanıt üretemediyse, bir sonraki bildirime geç.
            if not response_text:
                print(f"-> {author_acct} kullanıcısına yanıt üretilemediği için geçiliyor.")
                continue

            # Yanıtı gönder
            try:
                mastodon.status_post(
                    status=f"@{author_acct} {response_text}",
                    in_reply_to_id=status["id"]
                )
                print(f"✔️ Yanıt gönderildi: @{author_acct}")
            except Exception as e:
                print(f"❌ Yanıt gönderme hatası: {e}")

        # Son işlenen bildirimin ID'sini kaydet.
        save_last_mention_id(notification["id"])


def main():
    """Ana bot döngüsü."""
    print("🤖 Otomed.ai Bot Başlatılıyor...")

    # Gerekli ortam değişkenlerinin varlığını kontrol et
    if not all([HYPERBOLIC_API_KEY, MASTODON_ACCESS_TOKEN, MASTODON_API_BASE_URL]):
        print("❌ HATA: Gerekli .env değişkenleri bulunamadı!")
        print("Lütfen HYPERBOLIC_API_KEY, MASTODON_ACCESS_TOKEN, ve MASTODON_API_BASE_URL değişkenlerini .env dosyanızda ayarlayın.")
        return

    try:
        mastodon = Mastodon(
            access_token=MASTODON_ACCESS_TOKEN,
            api_base_url=MASTODON_API_BASE_URL
        )
        
        # Botun kullanıcı adını doğrula ve al
        bot_account = mastodon.account_verify_credentials()
        bot_username = bot_account["acct"]
        print(f"✔️ Mastodon'a başarıyla giriş yapıldı: @{bot_username}")
        print("----------------------------------------------------")

    except Exception as e:
        print(f"❌ Mastodon'a bağlanırken kritik bir hata oluştu: {e}")
        print("Lütfen MASTODON_ACCESS_TOKEN ve MASTODON_API_BASE_URL bilgilerinizi kontrol edin.")
        return

    # Ana döngü
    while True:
        try:
            process_mentions(mastodon, bot_username)
            # API limitlerini aşmamak için bekleme süresi
            print(f"-> Yeni bildirimler için 15 saniye bekleniyor...")
            time.sleep(15)
        except KeyboardInterrupt:
            print("\n👋 Bot kullanıcı tarafından durduruldu. Hoşçakal!")
            break
        except Exception as e:
            print(f"🐛 Beklenmedik bir sistem hatası oluştu: {e}")
            print("-> 60 saniye sonra yeniden denenecek...")
            time.sleep(60)

if __name__ == "__main__":
    main()