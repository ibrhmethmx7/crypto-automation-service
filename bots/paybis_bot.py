import os
import sys
import time
import json
import uuid
import signal
import traceback
import argparse
import tempfile
import shutil

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.keys import Keys
    import imaplib
    import email
    from email.header import decode_header
    import re
    import base64
    import requests
except ImportError as e:
    print(f"Error: Missing dependencies. Run: pip install selenium webdriver-manager requests")
    sys.exit(1)

# Gmail API için opsiyonel import
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GMAIL_API_AVAILABLE = True
except ImportError:
    GMAIL_API_AVAILABLE = False

# Global JSON mode flag
JSON_MODE = False

def send_to_node(message_type, data_payload):
    """Node.js'e yapısal JSON mesajı gönderir."""
    if JSON_MODE:
        print(json.dumps({"type": message_type, "data": data_payload}), flush=True)
    else:
        print(f"[{message_type}] {data_payload}", flush=True)

class PaybisBot:
    def __init__(self, url, amount_eur, wallet_address, card_info, customer_info, order_id, email_config=None):
        self.order_id = order_id
        self.driver = None
        self.temp_dir = None
        
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Paybis bot başlatılıyor...", "level": "info"})
        
        self.url = url or "https://paybis.com/"
        self.amount_eur = amount_eur
        self.wallet_address = wallet_address
        self.card_info = card_info
        self.customer_info = customer_info
        self.email = self.customer_info['email']
        self.email_config = email_config  # Email IMAP ayarları
        
        # Signal handler ekle
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self._setup_chrome()
    
    def _signal_handler(self, signum, frame):
        """Signal handler - temizlik yapar"""
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Signal {signum} alındı, temizlik yapılıyor...", "level": "warn"})
        self.cleanup()
        sys.exit(1)
    
    def _setup_chrome(self):
        """Chrome'u WebDriver Manager ile kurar"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Chrome kurulum denemesi {attempt + 1}/{max_retries}", "level": "info"})
                
                # Chrome options
                chrome_options = webdriver.ChromeOptions()
                
                # Temel ayarlar
                chrome_options.add_argument("--no-sandbox")
                chrome_options.add_argument("--disable-dev-shm-usage")
                chrome_options.add_argument("--disable-gpu")
                chrome_options.add_argument("--window-size=1920,1080")
                
                # Production/Railway için headless
                if os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('NODE_ENV') == 'production':
                    chrome_options.add_argument("--headless")
                    chrome_options.add_argument("--disable-web-security")
                    chrome_options.add_argument("--single-process")
                    chrome_options.add_argument("--no-zygote")
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Headless mode aktif", "level": "info"})
                
                # Unique temp directory
                unique_id = str(uuid.uuid4())[:8]
                self.temp_dir = tempfile.mkdtemp(prefix=f"chrome_paybis_{self.order_id}_{unique_id}_")
                chrome_options.add_argument(f"--user-data-dir={self.temp_dir}")
                
                # Anti-detection
                chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
                chrome_options.add_experimental_option('useAutomationExtension', False)
                
                # Performans ayarları
                chrome_options.add_argument("--disable-background-timer-throttling")
                chrome_options.add_argument("--disable-backgrounding-occluded-windows")
                chrome_options.add_argument("--disable-renderer-backgrounding")
                chrome_options.add_argument("--disable-features=TranslateUI")
                chrome_options.add_argument("--disable-extensions")
                chrome_options.add_argument("--disable-plugins")
                chrome_options.add_argument("--disable-images")  # Hızlandırmak için
                
                # Memory optimization
                chrome_options.add_argument("--memory-pressure-off")
                chrome_options.add_argument("--max_old_space_size=1024")
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ChromeDriver indiriliyor...", "level": "info"})
                
                # WebDriver Manager ile driver kurulumu
                try:
                    service = Service(ChromeDriverManager().install())
                    self.driver = webdriver.Chrome(service=service, options=chrome_options)
                except Exception as wdm_error:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] WebDriverManager hatası: {wdm_error}", "level": "warn"})
                    # Fallback: sistem Chrome'u dene
                    self.driver = webdriver.Chrome(options=chrome_options)
                
                # Anti-detection script
                self.driver.execute_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });
                """)
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Chrome başarıyla başlatıldı!", "level": "success"})
                return
                
            except Exception as e:
                error_msg = str(e)
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Chrome kurulum hatası (deneme {attempt + 1}): {error_msg}", "level": "error"})
                
                # Cleanup yap
                try:
                    if self.driver:
                        self.driver.quit()
                        self.driver = None
                except:
                    pass
                
                try:
                    if self.temp_dir and os.path.exists(self.temp_dir):
                        shutil.rmtree(self.temp_dir, ignore_errors=True)
                        self.temp_dir = None
                except:
                    pass
                
                if attempt < max_retries - 1:
                    sleep_time = (attempt + 1) * 2
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {sleep_time} saniye bekledikten sonra tekrar denenecek...", "level": "info"})
                    time.sleep(sleep_time)
                else:
                    send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Chrome {max_retries} denemede de başlatılamadı: {error_msg}"})
                    raise Exception(f"Chrome setup failed after {max_retries} attempts: {error_msg}")

    def wait_for_page_load(self, timeout=30):
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Sayfa tamamen yüklendi.", "level": "debug"})
            return True
        except TimeoutException:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Sayfa yükleme zaman aşımına uğradı."})
            return False

    def get_email_otp_code(self, max_attempts=10, delay=15):
        """Email'den OTP kodunu otomatik olarak al"""
        if not self.email_config:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Email config yok, manuel kod girişi bekleniyor.", "level": "warn"})
            return None
            
        # Gmail API varsa ve credentials varsa onu kullan
        if self.email_config.get('use_gmail_api') and GMAIL_API_AVAILABLE:
            return self.get_email_otp_code_gmail_api(max_attempts, delay)
        else:
            return self.get_email_otp_code_imap(max_attempts, delay)

    def get_email_otp_code_gmail_api(self, max_attempts=10, delay=15):
        """Gmail API ile OTP kodu al"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Gmail API ile OTP kodu aranıyor...", "level": "info"})
            
            # Gmail API credentials
            credentials_file = self.email_config.get('credentials_file', 'credentials.json')
            token_file = self.email_config.get('token_file', 'token.json')
            
            creds = None
            SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
            
            # Token dosyası var mı kontrol et
            if os.path.exists(token_file):
                creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            
            # Geçerli credentials yok ise yeniden auth yap
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    if not os.path.exists(credentials_file):
                        send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Gmail credentials.json dosyası bulunamadı!"})
                        return None
                    
                    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                    creds = flow.run_local_server(port=0)
                
                # Token'ı kaydet
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
            
            service = build('gmail', 'v1', credentials=creds)
            
            for attempt in range(max_attempts):
                try:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Gmail API kontrol deneme {attempt + 1}/{max_attempts}", "level": "debug"})
                    
                    # Paybis'den gelen mail'leri ara
                    query = 'from:(noreply@paybis.com OR support@paybis.com) newer_than:10m'
                    results = service.users().messages().list(userId='me', q=query, maxResults=10).execute()
                    messages = results.get('messages', [])
                    
                    for message in messages:
                        msg = service.users().messages().get(userId='me', id=message['id']).execute()
                        
                        # Email body'sini al
                        body = self.extract_gmail_api_body(msg)
                        
                        # OTP kod ara
                        otp_code = self.find_otp_in_text(body)
                        if otp_code:
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Gmail API'den OTP kodu bulundu: {otp_code}", "level": "success"})
                            return otp_code
                    
                    if attempt < max_attempts - 1:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] OTP bulunamadı, {delay} saniye bekleyip tekrar denenecek...", "level": "debug"})
                        time.sleep(delay)
                        
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Gmail API okuma hatası: {str(e)}", "level": "warn"})
                    if attempt < max_attempts - 1:
                        time.sleep(5)
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Gmail API'den OTP kodu bulunamadı.", "level": "warn"})
            return None
            
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Gmail API hatası: {str(e)}"})
            return None

    def extract_gmail_api_body(self, msg):
        """Gmail API message'ından body çıkar"""
        body = ""
        try:
            payload = msg['payload']
            
            if 'parts' in payload:
                for part in payload['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body']['data']
                        body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                        break
                    elif part['mimeType'] == 'text/html':
                        data = part['body']['data']
                        body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            else:
                if payload['mimeType'] == 'text/plain':
                    data = payload['body']['data']
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Gmail API body çıkarma hatası: {str(e)}", "level": "warn"})
        
        return body

    def get_email_otp_code_imap(self, max_attempts=10, delay=15):
        """IMAP ile OTP kodu al (App Password kullanarak)"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] IMAP ile OTP kodu aranıyor...", "level": "info"})
            
            # Email sunucu bilgileri
            imap_server = self.email_config.get('imap_server', 'imap.gmail.com')
            imap_port = self.email_config.get('imap_port', 993)
            email_address = self.email_config.get('email', self.email)
            email_password = self.email_config.get('app_password')  # App Password (16 haneli)
            
            if not email_password:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Gmail App Password bulunamadı!"})
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Gmail App Password almak için: Google Account → Security → 2-Step Verification → App passwords", "level": "info"})
                return None
            
            for attempt in range(max_attempts):
                try:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] IMAP kontrol deneme {attempt + 1}/{max_attempts}", "level": "debug"})
                    
                    # IMAP bağlantısı
                    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
                    mail.login(email_address, email_password)
                    mail.select('inbox')
                    
                    # Paybis'den gelen son mail'leri ara (daha basit format)
                    # Önce tüm yeni mail'leri al sonra filtrele
                    result, message_ids = mail.search(None, 'ALL')
                    
                    if result == 'OK' and message_ids[0]:
                        # En son gelen mail'leri kontrol et
                        ids = message_ids[0].split()
                        
                        # Son 10 mail'i kontrol et
                        for i in range(min(10, len(ids))):
                            msg_id = ids[-(i+1)]  # En yeniden başla
                            
                            result, msg_data = mail.fetch(msg_id, '(RFC822)')
                            if result == 'OK':
                                email_body = msg_data[0][1]
                                email_message = email.message_from_bytes(email_body)
                                
                                # From ve Subject kontrol et
                                from_addr = email_message.get('From', '').lower()
                                subject = email_message.get('Subject', '').lower()
                                
                                # Paybis mail'i mi kontrol et
                                if ('paybis' in from_addr or 
                                    'verification' in subject or 
                                    'code' in subject):
                                    
                                    # Email içeriğini al
                                    body = self.extract_email_body(email_message)
                                    
                                    # OTP kod ara
                                    otp_code = self.find_otp_in_text(body)
                                    if otp_code:
                                        mail.close()
                                        mail.logout()
                                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] IMAP'den OTP kodu bulundu: {otp_code}", "level": "success"})
                                        return otp_code
                    
                    mail.close()
                    mail.logout()
                    
                    if attempt < max_attempts - 1:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] OTP bulunamadı, {delay} saniye bekleyip tekrar denenecek...", "level": "debug"})
                        time.sleep(delay)
                    
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] IMAP okuma hatası: {str(e)}", "level": "warn"})
                    if attempt < max_attempts - 1:
                        time.sleep(5)
                        
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] IMAP'den OTP kodu bulunamadı.", "level": "warn"})
            return None
            
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] IMAP hatası: {str(e)}"})
            return None

    def find_otp_in_text(self, text):
        """Text içinde 6 haneli OTP kodunu bul"""
        if not text:
            return None
            
        # 6 haneli kod ara
        otp_patterns = [
            r'\b(\d{6})\b',  # 6 haneli sayı
            r'code[:\s]*(\d{6})',  # "code: 123456"
            r'verification[:\s]*(\d{6})',  # "verification: 123456"
            r'(\d{6})\s*is your verification',  # "123456 is your verification"
            r'your code is[:\s]*(\d{6})',  # "your code is: 123456"
        ]
        
        for pattern in otp_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                otp_code = matches[0]
                if len(otp_code) == 6 and otp_code.isdigit():
                    return otp_code
        
        return None

    def extract_email_body(self, email_message):
        """Email içeriğini çıkar"""
        body = ""
        try:
            if email_message.is_multipart():
                for part in email_message.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
                    elif part.get_content_type() == "text/html":
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
            else:
                body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Email body çıkarma hatası: {str(e)}", "level": "warn"})
        
        return body

    def initialize_purchase(self):
        """İlk adım: Ana sayfadan başlayıp miktar/currency seçimi"""
        send_to_node("progress", {"progress": 10, "step": "Paybis sayfasına gidiliyor..."})
        try:
            self.driver.get(self.url)
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {self.url} adresine gidildi", "level": "info"})
            if not self.wait_for_page_load(): 
                return False

            time.sleep(3)  # Sayfanın yüklenmesi için bekle

            # Amount input - güncellenen selector
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Miktar input'u aranıyor...", "level": "debug"})
            amount_selectors = [
                "input#exchange-form-from",
                "input[data-testid='amount-fiat']",
                ".input[id='exchange-form-from']",
                "input[placeholder='0.00']:first-of-type"
            ]
            
            amount_input = None
            for selector in amount_selectors:
                try:
                    amount_input = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Miktar input bulundu: {selector}", "level": "debug"})
                    break
                except:
                    continue
            
            if not amount_input:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Miktar input bulunamadı!"})
                return False

            # Miktar girme
            amount_input.clear()
            time.sleep(1)
            amount_input.send_keys(str(self.amount_eur))
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Miktar girildi: {self.amount_eur}", "level": "info"})
            time.sleep(2)

            # Currency seçimleri kontrol et - TRY zaten seçili olmalı, BTC de seçili olmalı
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Para birimleri kontrol ediliyor...", "level": "debug"})
            
            # Sayfada TRY ve BTC'nin seçili olup olmadığını kontrol et
            page_content = self.driver.page_source
            if "TRY" not in page_content or "BTC" not in page_content:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] TRY veya BTC bulunamadı, currency seçimleri yapılıyor...", "level": "warn"})
                
                # From currency (TRY) dropdown'ını kontrol et
                try:
                    from_currency_dropdown = self.driver.find_element(By.CSS_SELECTOR, ".form-layout__item-from .exchange-money-service")
                    if "TRY" not in from_currency_dropdown.text:
                        from_currency_dropdown.click()
                        time.sleep(2)
                        # TRY seçeneğini ara
                        try_option = WebDriverWait(self.driver, 10).until(
                            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'TRY')] | //div[contains(text(), 'TRY')]"))
                        )
                        try_option.click()
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] TRY seçildi.", "level": "info"})
                        time.sleep(1)
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] TRY seçimi sırasında hata (zaten seçili olabilir): {str(e)}", "level": "debug"})
                
                # To currency (BTC) dropdown'ını kontrol et
                try:
                    to_currency_dropdown = self.driver.find_element(By.CSS_SELECTOR, ".form-layout__item-to .exchange-money-service")
                    if "BTC" not in to_currency_dropdown.text:
                        to_currency_dropdown.click()
                        time.sleep(2)
                        # BTC seçeneğini ara
                        btc_option = WebDriverWait(self.driver, 10).until(
                            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'BTC')] | //div[contains(text(), 'Bitcoin')] | //div[contains(text(), 'BTC')]"))
                        )
                        btc_option.click()
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] BTC seçildi.", "level": "info"})
                        time.sleep(1)
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] BTC seçimi sırasında hata (zaten seçili olabilir): {str(e)}", "level": "debug"})

            # Buy Bitcoin button - güncellenen selector
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Buy Bitcoin butonu aranıyor...", "level": "debug"})
            button_selectors = [
                "button.exchange-form-action",
                "button[data-testid='buy-button']",
                "//button[contains(text(), 'Buy Bitcoin')]",
                "//button[contains(text(), 'Buy')]",
                ".form-layout__action button"
            ]
            
            buy_button = None
            for selector in button_selectors:
                try:
                    if selector.startswith("//"):
                        buy_button = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )
                    else:
                        buy_button = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Buy butonu bulundu: {selector}", "level": "debug"})
                    break
                except:
                    continue
            
            if not buy_button:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Buy Bitcoin butonu bulunamadı!"})
                return False

            # Buy butonuna tıkla
            self.driver.execute_script("arguments[0].scrollIntoView(true);", buy_button)
            time.sleep(1)
            buy_button.click()
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Buy Bitcoin butonuna tıklandı.", "level": "info"})
            time.sleep(3)
            
            return True
            
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Başlangıç adımında hata: {str(e)}"})
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Hata detayı: {traceback.format_exc()}", "level": "debug"})
            return False

    def enter_email(self):
        """İkinci adım: Email girişi"""
        send_to_node("progress", {"progress": 20, "step": "Email giriliyor..."})
        try:
            time.sleep(3)  # Yeni sayfa yüklenmesi için bekle
            
            # Email input'unu bul
            email_selectors = [
                "input[name='email']",
                "input[type='email']",
                "input[data-testid='email']",
                ".form-input__input[type='email']"
            ]
            
            email_input = None
            for selector in email_selectors:
                try:
                    email_input = WebDriverWait(self.driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Email input bulundu: {selector}", "level": "debug"})
                    break
                except:
                    continue
            
            if not email_input:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Email input bulunamadı!"})
                return False

            # Email'i gir
            email_input.clear()
            email_input.send_keys(self.email)
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Email girildi: {self.email}", "level": "info"})
            time.sleep(2)

            # Continue butonunu bul ve tıkla
            continue_selectors = [
                "button.btn.btn-primary.btn-lg",
                "//button[contains(text(), 'Continue')]",
                ".auth-app-footer button",
                "button[data-testid='continue']"
            ]
            
            continue_button = None
            for selector in continue_selectors:
                try:
                    if selector.startswith("//"):
                        continue_button = WebDriverWait(self.driver, 10).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )
                    else:
                        continue_button = WebDriverWait(self.driver, 10).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Continue butonu bulundu: {selector}", "level": "debug"})
                    break
                except:
                    continue
            
            if continue_button:
                continue_button.click()
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Continue butonuna tıklandı.", "level": "info"})
                time.sleep(3)
                return True
            else:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Continue butonu bulunamadı!"})
                return False
                
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Email girme hatası: {str(e)}"})
            return False

    def verify_email_otp(self):
        """Üçüncü adım: Email OTP doğrulama"""
        send_to_node("progress", {"progress": 35, "step": "Email doğrulanıyor..."})
        try:
            time.sleep(3)  # OTP sayfasının yüklenmesi için bekle
            
            # OTP gerekli mi kontrol et
            page_content = self.driver.page_source.lower()
            if "verify email" not in page_content and "otp" not in page_content:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Email OTP gerekli değil, bir sonraki adıma geçiliyor.", "level": "info"})
                return True

            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Email OTP sayfası tespit edildi.", "level": "info"})
            
            verification_code = None
            
            # Önce email'den otomatik kod almaya çalış
            if self.email_config:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Email'den otomatik OTP kodu alınıyor...", "level": "info"})
                verification_code = self.get_email_otp_code(max_attempts=8, delay=10)
            
            # Email'den kod alınamazsa manuel isteme
            if not verification_code:
                send_to_node("verification_required", {
                    "gatewayName": "Paybis",
                    "type": "email_otp",
                    "timeLimit": 300,
                    "message": f"Email verification code sent to {self.email}. Please check your email and enter the 6-digit code."
                })

                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel Email OTP bekleniyor...", "level": "info"})
                
                # Node.js'den kodu al
                code_from_node_json = sys.stdin.readline()
                code_payload = json.loads(code_from_node_json)
                
                if code_payload.get("type") == "verification_code":
                    verification_code = code_payload.get("code")
                else:
                    send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Geçersiz OTP kodu alındı!"})
                    return False
            
            if not verification_code or len(verification_code) != 6:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Geçersiz OTP kodu!"})
                return False
                
            # 6 haneli kodu input'lara gir
            otp_inputs = self.driver.find_elements(By.CSS_SELECTOR, ".verification-code-input__item input")
            
            if len(otp_inputs) >= 6:
                for i, digit in enumerate(verification_code):
                    if i < len(otp_inputs):
                        otp_inputs[i].clear()
                        otp_inputs[i].send_keys(digit)
                        time.sleep(0.2)
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] OTP kodu girildi: {verification_code}", "level": "info"})
                time.sleep(3)  # Biraz daha bekle
                
                # Continue butonunu bekle (disabled olmaktan çıkması için)
                continue_selectors = [
                    "button.btn.btn-primary.btn-lg:not([disabled])",
                    "button.btn.btn-primary:not([disabled])",
                    "//button[contains(text(), 'Continue') and not(@disabled)]",
                    ".auth-app-footer button:not([disabled])",
                    "button:not([disabled])"
                ]
                
                continue_button = None
                for selector in continue_selectors:
                    try:
                        if selector.startswith("//"):
                            continue_button = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, selector))
                            )
                        else:
                            continue_button = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                            )
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] OTP Continue butonu bulundu: {selector}", "level": "debug"})
                        break
                    except:
                        continue
                
                if continue_button:
                    # JavaScript ile tıkla (daha güvenilir)
                    self.driver.execute_script("arguments[0].click();", continue_button)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] OTP Continue butonuna tıklandı.", "level": "info"})
                    time.sleep(5)  # Sayfa geçişi için bekle
                    
                    # Sayfa değişimini kontrol et
                    new_url = self.driver.current_url
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Yeni URL: {new_url}", "level": "debug"})
                    return True
                else:
                    send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] OTP Continue butonu bulunamadı veya aktif değil!"})
                    
                    # Sayfanın durumunu kontrol et
                    page_content = self.driver.page_source
                    if "error" in page_content.lower() or "invalid" in page_content.lower():
                        send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] OTP kodu geçersiz olabilir!"})
                    
                    return False
            else:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] OTP input'ları bulunamadı!"})
                return False
                
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Email OTP doğrulama hatası: {str(e)}"})
            return False

    def select_wallet(self):
        """Dördüncü adım: Wallet seçimi veya Payment method sayfası kontrolü"""
        send_to_node("progress", {"progress": 50, "step": "Ödeme yöntemi kontrol ediliyor..."})
        try:
            time.sleep(3)  # Sayfa yüklenmesi için bekle
            
            current_url = self.driver.current_url
            page_content = self.driver.page_source.lower()
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Mevcut sayfa URL: {current_url}", "level": "debug"})
            
            # Intro popup'ını kapat
            self.close_intro_popup()
            
            # Payment sayfası mı kontrol et (Google Pay, New card vs.)
            if ("google pay" in page_content or "new card" in page_content or 
                "payment" in page_content.lower() and "method" in page_content.lower()):
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Payment method sayfası tespit edildi, New card seçiliyor...", "level": "info"})
                
                # New card seçeneğini bul ve tıkla
                new_card_selectors = [
                    "//label[contains(text(), 'New card')]",
                    "//div[contains(text(), 'New card')]", 
                    "//span[contains(text(), 'New card')]",
                    "input[value='new_card']",
                    "input[type='radio']:not(:checked)",  # Seçili olmayan radio
                    ".payment-option:contains('New card')",
                    "//div[contains(@class, 'payment') and contains(text(), 'New card')]"
                ]
                
                new_card_selected = False
                for selector in new_card_selectors:
                    try:
                        if selector.startswith("//"):
                            elements = self.driver.find_elements(By.XPATH, selector)
                        else:
                            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        for element in elements:
                            if element.is_displayed() and element.is_enabled():
                                # Element'e scroll
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                                time.sleep(1)
                                
                                # Tıkla
                                try:
                                    element.click()
                                except:
                                    self.driver.execute_script("arguments[0].click();", element)
                                
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card seçildi.", "level": "info"})
                                new_card_selected = True
                                time.sleep(2)
                                break
                        
                        if new_card_selected:
                            break
                    except Exception as e:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card selector hatası {selector}: {str(e)}", "level": "debug"})
                        continue
                
                if new_card_selected:
                    return True
                else:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card seçeneği bulunamadı, devam ediliyor...", "level": "warn"})
                    return True  # Devam et, belki otomatik seçili
            
            # Klasik wallet seçimi sayfası mı kontrol et
            elif "wallet" in page_content and "external" in page_content:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Wallet seçimi sayfası tespit edildi.", "level": "info"})
                
                # External wallet butonunu bul ve tıkla
                external_wallet_selectors = [
                    "button[data-testid='externalwallet']",
                    "//button[contains(text(), 'External wallet')]",
                    "//button[contains(text(), 'external wallet')]",
                    ".tabs__toggle:not(.is-selected)",
                    "//span[contains(text(), 'External')]/parent::button",
                    "[aria-label*='External']",
                    ".wallet-option:contains('External')",
                    "input[value='external']",
                    "//div[contains(text(), 'External')]/parent::*/parent::*//button"
                ]
                
                external_wallet_button = None
                for selector in external_wallet_selectors:
                    try:
                        if selector.startswith("//"):
                            external_wallet_button = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, selector))
                            )
                        else:
                            external_wallet_button = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                            )
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] External wallet butonu bulundu: {selector}", "level": "debug"})
                        break
                    except:
                        continue
                
                if external_wallet_button:
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", external_wallet_button)
                    time.sleep(1)
                    external_wallet_button.click()
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] External wallet seçildi.", "level": "info"})
                    time.sleep(2)
                else:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] External wallet butonu bulunamadı.", "level": "warn"})

                # Wallet adresi input'unu bul
                wallet_input_selectors = [
                    "input[data-testid='wallet-address']",
                    "input[name='wallet']",
                    "input[placeholder*='wallet' i]",
                    "input[placeholder*='address' i]",
                    ".form-wallet-select input",
                    "textarea[name='address']",
                    "input[type='text'][name*='address']",
                    "//input[contains(@placeholder, 'wallet')]",
                    "//input[contains(@placeholder, 'address')]"
                ]
                
                wallet_input = None
                for selector in wallet_input_selectors:
                    try:
                        if selector.startswith("//"):
                            wallet_input = WebDriverWait(self.driver, 5).until(
                                EC.presence_of_element_located((By.XPATH, selector))
                            )
                        else:
                            wallet_input = WebDriverWait(self.driver, 5).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                            )
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Wallet input bulundu: {selector}", "level": "debug"})
                        break
                    except:
                        continue
                
                if wallet_input:
                    wallet_input.clear()
                    wallet_input.send_keys(self.wallet_address)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Wallet adresi girildi.", "level": "info"})
                    time.sleep(2)
                else:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Wallet input bulunamadı.", "level": "warn"})

                # Continue butonunu bul ve tıkla
                continue_selectors = [
                    "button[data-testid='continuebutton']",
                    "button.btn.btn-lg.btn-primary",
                    "//button[contains(text(), 'Continue')]",
                    "//button[contains(text(), 'Next')]",
                    ".continue-button",
                    "button[type='submit']"
                ]
                
                continue_button = None
                for selector in continue_selectors:
                    try:
                        if selector.startswith("//"):
                            continue_button = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, selector))
                            )
                        else:
                            continue_button = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                            )
                        break
                    except:
                        continue
                
                if continue_button:
                    self.driver.execute_script("arguments[0].click();", continue_button)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Wallet Continue butonuna tıklandı.", "level": "info"})
                    time.sleep(3)
                    return True
                else:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Wallet Continue butonu bulunamadı.", "level": "warn"})
                    return True
            else:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Wallet/Payment sayfası tespit edilemedi, devam ediliyor.", "level": "info"})
                return True
                
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Wallet/Payment seçimi hatası: {str(e)}"})
            return True  # Hata olsa da devam et

    def select_new_card(self):
        """Beşinci adım: New card kontrolü (zaten seçilmiş olabilir)"""
        send_to_node("progress", {"progress": 65, "step": "Kart seçimi kontrol ediliyor..."})
        try:
            time.sleep(2)
            
            current_url = self.driver.current_url
            page_content = self.driver.page_source.lower()
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card kontrolü - URL: {current_url}", "level": "debug"})
            
            # Intro popup'ını kapat
            self.close_intro_popup()
            
            # Eğer zaten kart formu varsa (iframe), New card zaten seçilmiş demektir
            if "iframe" in page_content and ("cp.paybis.com" in page_content or "card" in page_content):
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Kart formu tespit edildi, New card zaten seçili.", "level": "info"})
                return True
            
            # Billing address formu varsa, payment aşamasındayız
            if ("billing" in page_content and "address" in page_content) or "country" in page_content:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Billing address formu tespit edildi, New card seçimi geçildi.", "level": "info"})
                return True
            
            # Hala New card seçimi gerekiyorsa (eski flow)
            if "new card" in page_content or "card-select" in page_content:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card seçimi gerekiyor...", "level": "info"})
                
                # New card seçeneğini bul - HTML yapısına göre güncel selector'lar
                new_card_selectors = [
                    # Ana hedef: New card placeholder'ı olan item'ın select button'u
                    "//div[contains(@class, 'card-select__item-placeholder') and contains(text(), 'New card')]/ancestor::div[contains(@class, 'card-select__item')]//button[contains(@class, 'card-select__toggle')]",
                    
                    # İkinci card-select item'ının button'u (New card genelde 2. sırada)
                    ".card-select__item:nth-child(2) .card-select__toggle",
                    ".card-select__item:last-child .card-select__toggle",
                    
                    # Seçili olmayan (is-selected class'ı olmayan) button
                    ".card-select__toggle:not(.is-selected)",
                    
                    # Direct text search
                    "//button[contains(@class, 'card-select__toggle') and preceding-sibling::*//text()[contains(., 'New card')]]",
                    
                    # Parent div approach
                    "//div[text()=' New card ']/ancestor::div[contains(@class, 'card-select__item')]//button",
                    
                    # CSS has approach (browser destekliyorsa)
                    ".card-select__item:has(.card-select__item-placeholder:contains('New card')) .card-select__toggle",
                ]
                
                new_card_element = None
                for selector in new_card_selectors:
                    try:
                        if selector.startswith("//"):
                            elements = self.driver.find_elements(By.XPATH, selector)
                        else:
                            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        for element in elements:
                            if element.is_displayed() and element.is_enabled():
                                new_card_element = element
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card elementi bulundu: {selector}", "level": "debug"})
                                break
                        
                        if new_card_element:
                            break
                    except Exception as e:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Selector hatası {selector}: {str(e)}", "level": "debug"})
                        continue
                
                # Eğer hala bulunamazsa basit yaklaşım: tüm card-select button'larını kontrol et
                if not new_card_element:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Tüm card-select button'ları kontrol ediliyor...", "level": "debug"})
                    
                    try:
                        all_buttons = self.driver.find_elements(By.CSS_SELECTOR, ".card-select__toggle")
                        for i, button in enumerate(all_buttons):
                            try:
                                # Button'ın parent container'ını bul
                                parent_container = button.find_element(By.XPATH, "./ancestor::div[contains(@class, 'card-select__item')]")
                                container_text = parent_container.text.lower()
                                
                                # "new card" içeriyorsa bu bizim button'umuz
                                if "new card" in container_text and button.is_displayed() and button.is_enabled():
                                    new_card_element = button
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card button bulundu (index {i+1})", "level": "debug"})
                                    break
                                    
                            except Exception as inner_e:
                                continue
                                
                    except Exception as e:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Card-select button kontrol hatası: {str(e)}", "level": "warn"})

                if new_card_element:
                    # JavaScript ile scroll ve tıklama
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", new_card_element)
                    time.sleep(1)
                    
                    try:
                        # Normal tıklama dene
                        new_card_element.click()
                    except:
                        # JavaScript ile tıklama dene
                        self.driver.execute_script("arguments[0].click();", new_card_element)
                    
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card seçildi.", "level": "info"})
                    time.sleep(3)
                    return True
                else:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card seçeneği bulunamadı, devam ediliyor.", "level": "warn"})
                    return True
            else:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] New card seçimi gerekli değil, devam ediliyor.", "level": "info"})
                return True
                
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] New card kontrolü hatası: {str(e)}"})
            return True  # Hata olsa da devam et

    def fill_card_details(self):
        """Altıncı adım: Kart bilgilerini doldur - iframe desteği ile"""
        send_to_node("progress", {"progress": 80, "step": "Kart bilgileri giriliyor..."})
        try:
            # Form açılmadan önce intro popup'ını kapat
            self.close_intro_popup()
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Kart formu bekleniliyor...", "level": "info"})
            
            # iframe kontrolü - Paybis kart formu iframe içinde
            iframe_found = False
            max_wait_time = 30
            
            for attempt in range(max_wait_time):
                try:
                    # Paybis iframe'ini ara
                    iframe_selectors = [
                        "iframe[src*='cp.paybis.com']",
                        "iframe.iframe",
                        "iframe[class*='iframe']",
                        ".card-form iframe",
                        "iframe"
                    ]
                    
                    for selector in iframe_selectors:
                        try:
                            iframes = self.driver.find_elements(By.CSS_SELECTOR, selector)
                            for iframe in iframes:
                                if iframe.is_displayed():
                                    iframe_src = iframe.get_attribute('src') or ''
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe bulundu: {iframe_src[:100]}", "level": "info"})
                                    
                                    # iframe'e geçiş yap
                                    self.driver.switch_to.frame(iframe)
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe'e geçiş yapıldı", "level": "info"})
                                    time.sleep(3)  # iframe'in yüklenmesi için bekle
                                    
                                    # iframe içinde input'ları kontrol et
                                    inputs_in_iframe = self.driver.find_elements(By.TAG_NAME, "input")
                                    visible_inputs = [inp for inp in inputs_in_iframe if inp.is_displayed()]
                                    
                                    if visible_inputs:
                                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe içinde {len(visible_inputs)} input bulundu", "level": "info"})
                                        iframe_found = True
                                        break
                                    else:
                                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe içinde input bulunamadı, ana frame'e dönülüyor", "level": "debug"})
                                        self.driver.switch_to.default_content()
                                        
                            if iframe_found:
                                break
                        except Exception as selector_error:
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe selector hatası {selector}: {str(selector_error)}", "level": "debug"})
                            # Ana frame'e geri dön
                            try:
                                self.driver.switch_to.default_content()
                            except:
                                pass
                            continue
                    
                    if iframe_found:
                        break
                    
                    # iframe henüz yüklenmemişse bekle
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe henüz hazır değil, bekleniyor... ({attempt+1}/{max_wait_time})", "level": "debug"})
                    time.sleep(1)
                    
                except Exception as general_error:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe arama hatası: {str(general_error)}", "level": "debug"})
                    try:
                        self.driver.switch_to.default_content()
                    except:
                        pass
                    time.sleep(1)
                    continue
            
            if not iframe_found:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe bulunamadı, ana sayfada form aranıyor...", "level": "warn"})
                
                # iframe bulunamazsa ana sayfada input ara (fallback)
                all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
                visible_inputs = [inp for inp in all_inputs if inp.is_displayed() and inp.is_enabled()]
                
                if not visible_inputs:
                    send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Hiçbir input bulunamadı!"})
                    return False
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Ana sayfada {len(visible_inputs)} input bulundu", "level": "info"})
            
            # Şimdi iframe içinde (veya ana sayfada) kart bilgilerini doldur
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Kart bilgileri giriliyor...", "level": "info"})
            
            # Card number
            card_filled = self.fill_card_number_iframe()
            if not card_filled:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Kart numarası doldurma başarısız!"})
                self.driver.switch_to.default_content()
                return False

            # Expiry date
            expiry_filled = self.fill_expiry_iframe()
            if not expiry_filled:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Expiry date doldurma başarısız!", "level": "warn"})

            # CVV
            cvv_filled = self.fill_cvv_iframe()
            if not cvv_filled:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] CVV doldurma başarısız!", "level": "warn"})

            # iframe'den çık ve ana frame'e dön
            self.driver.switch_to.default_content()
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Ana frame'e geri dönüldü", "level": "info"})
            time.sleep(2)

            # Billing address ana sayfada doldur
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Billing address doldurma başlıyor...", "level": "info"})
            billing_success = self.fill_billing_address()
            
            if not billing_success:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Billing address doldurma başarısız!"})
                return False

            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Kart bilgileri ve billing address tamamlandı.", "level": "success"})
            return True
            
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Kart bilgileri girme hatası: {str(e)}"})
            return False
        finally:
            # Her durumda ana frame'e dön
            try:
                self.driver.switch_to.default_content()
            except:
                pass

    def fill_card_number_iframe(self):
        """iframe içinde kart numarası doldur"""
        try:
            card_selectors = [
                # iframe içi spesifik
                "input[name='number']",
                "input[id='number']", 
                "input[data-testid='card-number']",
                "input[placeholder*='card number' i]",
                "input[placeholder*='number' i]",
                "input[autocomplete='cc-number']",
                
                # Genel
                "input[name*='card' i]",
                "input[id*='card' i]",
                "input[type='text']:nth-of-type(1)",
                "input[type='text']"
            ]
            
            for selector in card_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            # Scroll ve focus
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                            self.driver.execute_script("arguments[0].focus();", element)
                            time.sleep(1)
                            
                            # Clear ve input
                            element.clear()
                            element.send_keys(self.card_info['card_number'])
                            
                            # Events trigger
                            self.driver.execute_script("""
                                arguments[0].dispatchEvent(new Event('input', {bubbles: true}));
                                arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
                                arguments[0].dispatchEvent(new Event('blur', {bubbles: true}));
                            """, element)
                            
                            # Değer kontrolü
                            current_value = element.get_attribute('value')
                            if current_value:
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Kart numarası girildi: {current_value[:4]}****", "level": "success"})
                                time.sleep(2)
                                return True
                            
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Card selector hatası {selector}: {str(e)}", "level": "debug"})
                    continue
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Kart numarası input'u bulunamadı!", "level": "warn"})
            return False
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Kart numarası doldurma hatası: {str(e)}", "level": "debug"})
            return False

    def fill_expiry_iframe(self):
        """iframe içinde expiry date doldur"""
        try:
            expiry_selectors = [
                "input[name='expiry']",
                "input[id='expiry']",
                "input[name='exp']",
                "input[id='exp']",
                "input[data-testid='expiry']",
                "input[placeholder*='expiry' i]",
                "input[placeholder*='mm/yy' i]",
                "input[placeholder*='exp' i]",
                "input[autocomplete='cc-exp']",
                "input[name*='expiry' i]",
                "input[name*='exp' i]"
            ]
            
            for selector in expiry_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            self.driver.execute_script("arguments[0].focus();", element)
                            element.clear()
                            element.send_keys(self.card_info['expiry_date'])
                            
                            self.driver.execute_script("""
                                arguments[0].dispatchEvent(new Event('input', {bubbles: true}));
                                arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
                                arguments[0].dispatchEvent(new Event('blur', {bubbles: true}));
                            """, element)
                            
                            current_value = element.get_attribute('value')
                            if current_value:
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Expiry date girildi: {current_value}", "level": "success"})
                                return True
                            
                except Exception as e:
                    continue
            
            return False
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Expiry doldurma hatası: {str(e)}", "level": "debug"})
            return False

    def fill_cvv_iframe(self):
        """iframe içinde CVV doldur"""
        try:
            cvv_selectors = [
                "input[name='cvv']",
                "input[id='cvv']",
                "input[name='cvc']", 
                "input[id='cvc']",
                "input[data-testid='cvv']",
                "input[placeholder*='cvv' i]",
                "input[placeholder*='cvc' i]",
                "input[placeholder*='security' i]",
                "input[autocomplete='cc-csc']",
                "input[name*='cvv' i]",
                "input[name*='cvc' i]",
                "input[type='password']"
            ]
            
            for selector in cvv_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            self.driver.execute_script("arguments[0].focus();", element)
                            element.clear()
                            element.send_keys(self.card_info['cvv'])
                            
                            self.driver.execute_script("""
                                arguments[0].dispatchEvent(new Event('input', {bubbles: true}));
                                arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
                                arguments[0].dispatchEvent(new Event('blur', {bubbles: true}));
                            """, element)
                            
                            current_value = element.get_attribute('value')
                            if current_value:
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] CVV girildi", "level": "success"})
                                return True
                            
                except Exception as e:
                    continue
            
            return False
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] CVV doldurma hatası: {str(e)}", "level": "debug"})
            return False

    def debug_page_structure(self):
        """Sayfa yapısını debug et"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Sayfa yapısı debug ediliyor...", "level": "debug"})
            
            # JavaScript ile sayfa analizi
            page_info = self.driver.execute_script("""
                return {
                    url: window.location.href,
                    title: document.title,
                    selectCount: document.querySelectorAll('select').length,
                    inputCount: document.querySelectorAll('input').length,
                    formCount: document.querySelectorAll('form').length,
                    iframeCount: document.querySelectorAll('iframe').length,
                    hasCountryText: document.body.innerText.toLowerCase().includes('country'),
                    hasAddressText: document.body.innerText.toLowerCase().includes('address'),
                    customSelectCount: document.querySelectorAll('div.select').length
                };
            """)
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Sayfa bilgileri: {page_info}", "level": "debug"})
            
            # iframe detayları
            if page_info['iframeCount'] > 0:
                iframe_details = self.driver.execute_script("""
                    var iframes = document.querySelectorAll('iframe');
                    var details = [];
                    for(var i = 0; i < iframes.length; i++) {
                        var iframe = iframes[i];
                        var rect = iframe.getBoundingClientRect();
                        details.push({
                            index: i,
                            src: iframe.src || '',
                            className: iframe.className || '',
                            visible: rect.height > 0 && rect.width > 0,
                            width: rect.width,
                            height: rect.height
                        });
                    }
                    return details;
                """)
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] iframe detayları: {iframe_details}", "level": "debug"})
            
            # Custom select elementlerini incele
            if page_info['customSelectCount'] > 0:
                custom_select_details = self.driver.execute_script("""
                    var customSelects = document.querySelectorAll('div.select');
                    var details = [];
                    for(var i = 0; i < customSelects.length; i++) {
                        var select = customSelects[i];
                        var rect = select.getBoundingClientRect();
                        var searchInput = select.querySelector('input.select__search');
                        details.push({
                            index: i,
                            id: select.id || '',
                            className: select.className || '',
                            visible: rect.height > 0 && rect.width > 0,
                            hasSearchInput: !!searchInput,
                            searchInputValue: searchInput ? searchInput.value : ''
                        });
                    }
                    return details;
                """)
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Custom select elementleri: {custom_select_details}", "level": "debug"})
            
            # Select elementlerini detaylı incele
            if page_info['selectCount'] > 0:
                select_details = self.driver.execute_script("""
                    var selects = document.querySelectorAll('select');
                    var details = [];
                    for(var i = 0; i < selects.length; i++) {
                        var select = selects[i];
                        var rect = select.getBoundingClientRect();
                        details.push({
                            index: i,
                            id: select.id || '',
                            name: select.name || '',
                            className: select.className || '',
                            visible: rect.height > 0 && rect.width > 0,
                            optionCount: select.options.length,
                            firstOptionText: select.options.length > 0 ? select.options[0].text : '',
                            parentText: select.parentElement ? select.parentElement.innerText.slice(0, 100) : ''
                        });
                    }
                    return details;
                """)
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Select elementleri: {select_details}", "level": "debug"})
            
            # Form elementlerini incele
            if page_info['formCount'] > 0:
                form_details = self.driver.execute_script("""
                    var forms = document.querySelectorAll('form');
                    var details = [];
                    for(var i = 0; i < forms.length; i++) {
                        var form = forms[i];
                        details.push({
                            index: i,
                            id: form.id || '',
                            className: form.className || '',
                            selectsInForm: form.querySelectorAll('select').length,
                            inputsInForm: form.querySelectorAll('input').length,
                            customSelectsInForm: form.querySelectorAll('div.select').length,
                            formText: form.innerText.slice(0, 200)
                        });
                    }
                    return details;
                """)
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Form elementleri: {form_details}", "level": "debug"})
                
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Debug hatası: {str(e)}", "level": "debug"})

    def find_country_element_js(self):
        """JavaScript ile country elementi bul - Custom dropdown desteği ile"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript ile country aranıyor (custom dropdown desteği)...", "level": "debug"})
            
            # JavaScript ile element arama - Custom dropdown'a özel
            element_info = self.driver.execute_script("""
                // Custom dropdown component arama
                var methods = [];
                
                // Method 1: Paybis custom dropdown (div.select)
                var customSelects = document.querySelectorAll('div.select[id="country"], div.select[name="country"]');
                for(var i = 0; i < customSelects.length; i++) {
                    var rect = customSelects[i].getBoundingClientRect();
                    if(rect.height > 0 && rect.width > 0) {
                        methods.push({
                            method: 'custom_dropdown',
                            element: customSelects[i],
                            type: 'div_select'
                        });
                    }
                }
                
                // Method 2: Input içinde arama (custom dropdown'ın input'u)
                var searchInputs = document.querySelectorAll('input.select__search[autocomplete*="country"]');
                for(var i = 0; i < searchInputs.length; i++) {
                    var rect = searchInputs[i].getBoundingClientRect();
                    if(rect.height > 0 && rect.width > 0) {
                        methods.push({
                            method: 'search_input',
                            element: searchInputs[i].parentElement,
                            type: 'search_input_parent'
                        });
                    }
                }
                
                // Method 3: Label'dan custom dropdown bulma
                var labels = document.querySelectorAll('label');
                for(var i = 0; i < labels.length; i++) {
                    if(labels[i].innerText.toLowerCase().includes('country')) {
                        // Label'ın parent'ında div.select ara
                        var fieldDiv = labels[i].closest('.field');
                        if(fieldDiv) {
                            var customSelect = fieldDiv.querySelector('div.select');
                            if(customSelect) {
                                var rect = customSelect.getBoundingClientRect();
                                if(rect.height > 0 && rect.width > 0) {
                                    methods.push({
                                        method: 'label_to_custom',
                                        element: customSelect,
                                        type: 'label_custom'
                                    });
                                }
                            }
                        }
                    }
                }
                
                // Method 4: Standard select fallback
                var standardSelects = document.querySelectorAll('select');
                for(var i = 0; i < standardSelects.length; i++) {
                    var rect = standardSelects[i].getBoundingClientRect();
                    if(rect.height > 0 && rect.width > 0) {
                        methods.push({
                            method: 'standard_select',
                            element: standardSelects[i],
                            type: 'standard'
                        });
                    }
                }
                
                // İlk bulduğu visible element'i döndür
                for(var i = 0; i < methods.length; i++) {
                    var element = methods[i].element;
                    var rect = element.getBoundingClientRect();
                    if(rect.height > 0 && rect.width > 0) {
                        return {
                            found: true,
                            method: methods[i].method,
                            type: methods[i].type,
                            tagName: element.tagName,
                            id: element.id || '',
                            className: element.className || '',
                            index: i
                        };
                    }
                }
                
                return {found: false, totalMethods: methods.length};
            """)
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript arama sonucu: {element_info}", "level": "debug"})
            
            if element_info and element_info.get('found'):
                # Element'i WebDriver ile bul
                if element_info.get('id'):
                    try:
                        element = self.driver.find_element(By.ID, element_info['id'])
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country element (ID) bulundu: {element_info['id']} - Type: {element_info.get('type')}", "level": "info"})
                        return element
                    except:
                        pass
                
                # Class ile ara
                if element_info.get('className'):
                    try:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, f".{element_info['className'].split()[0]}")
                        for elem in elements:
                            if elem.is_displayed():
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country element (CLASS) bulundu - Type: {element_info.get('type')}", "level": "info"})
                                return elem
                    except:
                        pass
            
            return None
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript country arama hatası: {str(e)}", "level": "debug"})
            return None

    def find_country_element_traditional(self):
        """Geleneksel selector'larla country elementi bul - Custom dropdown desteği ile"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Geleneksel selector'larla country aranıyor (custom dropdown desteği)...", "level": "debug"})
            
            # Custom dropdown'lar için özel selector'lar
            custom_dropdown_selectors = [
                # Paybis custom dropdown
                "div.select[id='country']",
                "div.select[name='country']",
                "div[class*='select'][id*='country']",
                
                # Billing form custom dropdown
                ".billing-address-form .billing-address-form__item--country .select",
                ".billing-address-form__item--country div.select",
                
                # Vue.js data attributes ile
                "div[data-v-bf822650].select",
                
                # Input içinde arama (custom dropdown'ın search input'u)
                "input.select__search[autocomplete*='country']",
                "input[class*='select__search']",
                
                # Field wrapper içinden arama
                ".field .select",
                ".billing-address-form__item .select",
                
                # Wrapper içinden
                ".wrapper .select",
                ".wrapper div[id='country']"
            ]
            
            # Standard selector'lar
            standard_selectors = [
                # ID ve Name bazlı
                "select[id*='country' i]",
                "select[name*='country' i]",
                "input[id*='country' i]",
                "input[name*='country' i]",
                
                # Class bazlı
                "select[class*='country' i]",
                "input[class*='country' i]",
                ".country select",
                ".country input", 
                ".country-select",
                ".country-dropdown",
                ".billing-country select",
                ".billing-country input",
                ".address-country select",
                ".address-country input",
                
                # Data attribute bazlı
                "select[data-field*='country' i]",
                "select[data-name*='country' i]",
                "input[data-field*='country' i]",
                "input[data-name*='country' i]",
                
                # Placeholder bazlı
                "select[data-placeholder*='country' i]",
                "input[placeholder*='country' i]",
                "input[autocomplete*='country']",
                
                # Form içi arama
                "form select:nth-of-type(1)",  # İlk select
                "form select:last-of-type",   # Son select
                ".form select",
                ".billing-form select",
                ".address-form select",
                ".payment-form select",
                ".checkout-form select",
                
                # Genel select arama
                "select",  # Tüm select'ler
                "select:not([style*='display: none'])",  # Gizli olmayanlar
                "select:not([hidden])",  # Hidden olmayanlar
            ]
            
            # XPath selector'ları
            xpath_selectors = [
                # Text bazlı arama
                "//label[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'country')]/following::select[1]",
                "//label[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'country')]/following::input[1]",
                "//span[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'country')]/following::select[1]",
                "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'country')]/following::select[1]",
                "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'country')]//select",
                "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'country')]//input",
                
                # Custom dropdown XPath
                "//label[contains(text(), 'Country')]/following::div[contains(@class, 'select')][1]",
                "//div[contains(text(), 'Country')]/following::div[contains(@class, 'select')][1]",
                "//div[contains(@class, 'field') and .//label[contains(text(), 'Country')]]//div[contains(@class, 'select')]",
                
                # Fallback XPath
                "//select[position()=1]",  # İlk select
                "//select[last()]",        # Son select
                "//form//select[1]",       # Form'daki ilk select
                "//div[contains(@class, 'select')][position()=1]",  # İlk custom select
            ]
            
            # Önce custom dropdown'ları ara
            all_selectors = custom_dropdown_selectors + standard_selectors + xpath_selectors
            
            for selector in all_selectors:
                try:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Selector deneniyor: {selector}", "level": "debug"})
                    
                    elements = []
                    if selector.startswith("//"):
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            # Element tipini belirle
                            element_class = element.get_attribute('class') or ''
                            element_tag = element.tag_name.lower()
                            
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Görünür element bulundu: {selector} - Tag: {element_tag}, Class: {element_class[:50]}", "level": "info"})
                            return element
                            
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Selector hatası {selector}: {str(e)}", "level": "debug"})
                    continue
            
            return None
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Geleneksel country arama hatası: {str(e)}", "level": "debug"})
            return None

    def select_country(self, element):
        """Country elementini seç - Custom dropdown desteği ile"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country element seçimi deneniyor...", "level": "info"})
            
            # Element'e scroll
            self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
            time.sleep(2)
            
            # Element tipini belirle
            element_class = element.get_attribute('class') or ''
            element_tag = element.tag_name.lower()
            
            if 'select' in element_class and element_tag == 'div':
                # Custom dropdown (Paybis tarzı)
                return self.select_custom_dropdown(element)
            elif element_tag == 'select':
                # Standard dropdown
                return self.select_country_dropdown(element)
            else:
                # Input field
                return self.select_country_input(element)
                
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country seçim hatası: {str(e)}", "level": "debug"})
            return False

    def select_custom_dropdown(self, dropdown_element):
        """Custom dropdown - BASIT VE DIREKT"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Custom dropdown Turkey seçimi başlıyor...", "level": "info"})
            
            # Element bilgilerini logla
            element_id = dropdown_element.get_attribute('id')
            element_class = dropdown_element.get_attribute('class')
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Element: id={element_id}, class={element_class}", "level": "debug"})
            
            # Scroll to element
            self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", dropdown_element)
            time.sleep(2)
            
            # 1. Ana dropdown'a tıkla (aç)
            try:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Dropdown açılıyor...", "level": "debug"})
                dropdown_element.click()
                time.sleep(2)
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Dropdown açıldı!", "level": "success"})
            except Exception as click_error:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Normal click hatası: {str(click_error)}", "level": "debug"})
                # JavaScript ile dene
                self.driver.execute_script("arguments[0].click();", dropdown_element)
                time.sleep(2)
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript click ile açıldı!", "level": "success"})
            
            # 2. Search input'u bul ve Turkey yaz
            search_input_selectors = [
                "input.select__search",              # HTML'den direkt
                ".select__search",                   # Class ile
                "input[autocomplete='billing country']", # Autocomplete ile
                f"#{element_id} input",              # ID içinden input
                "input"                              # Son çare
            ]
            
            search_input = None
            for selector in search_input_selectors:
                try:
                    # Ana dropdown içinden ara
                    search_input = dropdown_element.find_element(By.CSS_SELECTOR, selector.replace(f"#{element_id} ", ""))
                    if search_input.is_displayed():
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Search input bulundu: {selector}", "level": "success"})
                        break
                except:
                    try:
                        # Global olarak ara
                        search_input = self.driver.find_element(By.CSS_SELECTOR, selector)
                        if search_input.is_displayed():
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Search input bulundu (global): {selector}", "level": "success"})
                            break
                    except:
                        continue
            
            if search_input:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Search input'a Turkey yazılıyor...", "level": "debug"})
                
                try:
                    # Focus
                    search_input.click()
                    time.sleep(0.5)
                    
                    # Clear ve type
                    search_input.clear()
                    search_input.send_keys("Turkey")
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 'Turkey' yazıldı!", "level": "success"})
                    time.sleep(1)
                    
                    # Enter tuşu
                    search_input.send_keys(Keys.ENTER)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Enter tuşuna basıldı!", "level": "success"})
                    time.sleep(2)
                    
                    # Değer kontrolü
                    current_value = search_input.get_attribute('value')
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Input değeri: '{current_value}'", "level": "debug"})
                    
                    if current_value and ('turkey' in current_value.lower() or 'tr' in current_value.lower()):
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ✅ Turkey başarıyla seçildi!", "level": "success"})
                        return True
                    else:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ⚠️ Değer beklendiği gibi değil, yine de devam ediliyor", "level": "warn"})
                        return True  # Yine de true döndür, çalışıyor olabilir
                        
                except Exception as input_error:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Search input yazma hatası: {str(input_error)}", "level": "debug"})
            
            # 3. Option'ları manuel ara (dropdown açıksa)
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel option arama...", "level": "debug"})
            
            option_selectors = [
                "//div[contains(text(), 'Turkey')]",
                "//li[contains(text(), 'Turkey')]", 
                "//span[contains(text(), 'Turkey')]",
                "//*[contains(text(), 'Turkey')]"
            ]
            
            for selector in option_selectors:
                try:
                    options = self.driver.find_elements(By.XPATH, selector)
                    for option in options:
                        if option.is_displayed() and 'turkey' in option.text.lower():
                            option.click()
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ✅ Option tıklandı: {option.text}", "level": "success"})
                            time.sleep(2)
                            return True
                except:
                    continue
            
            # 4. JavaScript ile zorla
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript ile zorla değer atama...", "level": "debug"})
            
            try:
                self.driver.execute_script("""
                    var element = arguments[0];
                    var input = element.querySelector('input') || element.querySelector('.select__search');
                    if(input) {
                        input.value = 'Turkey';
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                        console.log('JavaScript Turkey atandı');
                    }
                """, dropdown_element)
                
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ✅ JavaScript ile Turkey atandı!", "level": "success"})
                time.sleep(2)
                return True
                
            except Exception as js_error:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript hatası: {str(js_error)}", "level": "debug"})
            
            # Son çare olarak true döndür
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ⚠️ Kesin sonuç alınamadı ama devam ediliyor", "level": "warn"})
            return True
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Custom dropdown genel hatası: {str(e)}", "level": "debug"})
            return False

    def select_country_dropdown(self, select_element):
        """Dropdown'dan Turkey seç"""
        try:
            select_obj = Select(select_element)
            
            # Seçenekleri logla
            option_texts = [opt.text for opt in select_obj.options[:10]]  # İlk 10 seçeneği
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Dropdown seçenekleri: {option_texts}", "level": "debug"})
            
            # Turkey arama keyword'leri
            turkey_keywords = ['turkey', 'türkiye', 'turkiye', 'tr', 'turkish republic', 'turkish', 'tur']
            
            # Text ile ara
            for option in select_obj.options:
                option_text = option.text.lower().strip()
                if any(keyword in option_text for keyword in turkey_keywords):
                    try:
                        select_obj.select_by_visible_text(option.text)
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country seçildi (text): {option.text}", "level": "success"})
                        
                        # Change event trigger
                        self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", select_element)
                        time.sleep(2)
                        
                        # Seçim doğrulaması
                        current_selection = select_obj.first_selected_option.text
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Seçim doğrulandı: {current_selection}", "level": "info"})
                        return True
                    except Exception as select_error:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Text seçim hatası: {str(select_error)}", "level": "debug"})
                        continue
            
            # Value ile ara
            for option in select_obj.options:
                option_value = option.get_attribute('value').lower().strip()
                if any(keyword in option_value for keyword in turkey_keywords):
                    try:
                        select_obj.select_by_value(option.get_attribute('value'))
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country seçildi (value): {option.get_attribute('value')}", "level": "success"})
                        
                        self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", select_element)
                        time.sleep(2)
                        return True
                    except Exception as select_error:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Value seçim hatası: {str(select_error)}", "level": "debug"})
                        continue
            
            # Manuel click ile dene
            try:
                select_element.click()
                time.sleep(1)
                
                # Option elementlerini manuel ara
                option_elements = self.driver.find_elements(By.CSS_SELECTOR, "option")
                for opt_elem in option_elements:
                    if opt_elem.is_displayed() and any(keyword in opt_elem.text.lower() for keyword in turkey_keywords):
                        opt_elem.click()
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country seçildi (manuel): {opt_elem.text}", "level": "success"})
                        time.sleep(2)
                        return True
            except Exception as manual_error:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel seçim hatası: {str(manual_error)}", "level": "debug"})
            
            return False
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Dropdown seçim hatası: {str(e)}", "level": "debug"})
            return False

    def select_country_input(self, input_element):
        """Input'a Turkey yaz"""
        try:
            input_element.clear()
            input_element.send_keys("Turkey")
            
            # Events trigger
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles: true}));", input_element)
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", input_element)
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country input'a girildi: Turkey", "level": "success"})
            time.sleep(2)
            return True
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Input country hatası: {str(e)}", "level": "debug"})
            return False

    def try_manual_country_input(self):
        """Son çare: Country elementini manuel bulma ve Turkey yazma"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel country bulma başlıyor...", "level": "info"})
            
            # HTML'den bilinen tüm muhtemel elementleri ara
            manual_selectors = [
                # Direkt HTML selector'ları
                "#country",
                "div[id='country']",
                ".billing-address-form__item--country",
                "input[autocomplete='billing country']",
                ".select__search",
                
                # Parent-child ilişkisi
                ".billing-address-form__item--country .select",
                ".billing-address-form__item--country input",
                ".field .select",
                ".wrapper .select",
                
                # Genel arama
                "div.select",
                "input.select__search",
                ".select",
                
                # Fallback
                "input[type='text']",
                "select"
            ]
            
            for selector in manual_selectors:
                try:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel selector: {selector}", "level": "debug"})
                    
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for i, element in enumerate(elements):
                        try:
                            if not element.is_displayed() or not element.is_enabled():
                                continue
                            
                            # Element context'ini al
                            context = self.get_element_context(element)
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Element {i+1} context: {context}", "level": "debug"})
                            
                            # Country ile ilgili mi kontrol et
                            if self.is_country_element(element, context):
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country element tespit edildi: {selector}", "level": "info"})
                                
                                # Turkey yazmayı dene
                                if self.fill_country_element(element):
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel country doldurma başarılı!", "level": "success"})
                                    return True
                                    
                        except Exception as element_error:
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Element işleme hatası: {str(element_error)}", "level": "debug"})
                            continue
                            
                except Exception as selector_error:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel selector hatası {selector}: {str(selector_error)}", "level": "debug"})
                    continue
            
            # Hiçbiri çalışmazsa JavaScript injection
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript injection deneniyor...", "level": "info"})
            return self.inject_country_value()
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel country bulma hatası: {str(e)}", "level": "debug"})
            return False

    def get_element_context(self, element):
        """Element'in detaylı context'ini al"""
        try:
            context_parts = []
            
            # Basic attributes
            tag_name = element.tag_name.lower()
            element_id = element.get_attribute('id') or ''
            element_class = element.get_attribute('class') or ''
            element_name = element.get_attribute('name') or ''
            placeholder = element.get_attribute('placeholder') or ''
            autocomplete = element.get_attribute('autocomplete') or ''
            
            context_parts.append(f"tag:{tag_name}")
            if element_id:
                context_parts.append(f"id:{element_id}")
            if element_class:
                context_parts.append(f"class:{element_class[:30]}")
            if element_name:
                context_parts.append(f"name:{element_name}")
            if placeholder:
                context_parts.append(f"placeholder:{placeholder}")
            if autocomplete:
                context_parts.append(f"autocomplete:{autocomplete}")
            
            # Parent context
            try:
                parent = element.find_element(By.XPATH, "./parent::*")
                parent_class = parent.get_attribute('class') or ''
                if parent_class:
                    context_parts.append(f"parent_class:{parent_class[:30]}")
                    
                parent_text = parent.text[:50] if parent.text else ''
                if parent_text:
                    context_parts.append(f"parent_text:{parent_text}")
            except:
                pass
            
            # Preceding label
            try:
                labels = element.find_elements(By.XPATH, "./preceding::label | ./ancestor::*//label")
                for label in labels[:2]:  # İlk 2 label
                    label_text = label.text[:20] if label.text else ''
                    if label_text:
                        context_parts.append(f"label:{label_text}")
                        break
            except:
                pass
            
            return " | ".join(context_parts)
            
        except Exception as e:
            return f"context_error:{str(e)}"

    def is_country_element(self, element, context):
        """Element country ile ilgili mi kontrol et"""
        try:
            context_lower = context.lower()
            
            # Pozitif işaretler
            country_indicators = [
                'country', 'billing country', 'autocomplete:billing country',
                'id:country', 'name:country', 'label:country',
                'billing-address-form__item--country'
            ]
            
            # Negatif işaretler
            negative_indicators = [
                'card', 'cvv', 'expiry', 'number', 'email', 'phone'
            ]
            
            # Pozitif kontrol
            has_country_indicator = any(indicator in context_lower for indicator in country_indicators)
            
            # Negatif kontrol  
            has_negative_indicator = any(indicator in context_lower for indicator in negative_indicators)
            
            # Karar
            is_country = has_country_indicator and not has_negative_indicator
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country check: {is_country} (positive: {has_country_indicator}, negative: {has_negative_indicator})", "level": "debug"})
            
            return is_country
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country check hatası: {str(e)}", "level": "debug"})
            return False

    def fill_country_element(self, element):
        """Country elementi doldur"""
        try:
            tag_name = element.tag_name.lower()
            element_class = element.get_attribute('class') or ''
            
            # Scroll to element
            self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
            time.sleep(1)
            
            # Custom dropdown mu kontrol et
            if 'select' in element_class and tag_name == 'div':
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Custom dropdown tespit edildi", "level": "debug"})
                return self.select_custom_dropdown(element)
            
            # Input mu kontrol et
            elif tag_name == 'input':
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Input tespit edildi", "level": "debug"})
                
                # Custom dropdown'un input'u mu?
                if 'select__search' in element_class:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Custom dropdown search input tespit edildi", "level": "debug"})
                    
                    # Input'a direkt Turkey yaz
                    element.clear()
                    element.send_keys("Turkey")
                    element.send_keys(Keys.ENTER)
                    
                    # Events trigger
                    self.driver.execute_script("""
                        arguments[0].dispatchEvent(new Event('input', {bubbles: true}));
                        arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
                        arguments[0].dispatchEvent(new Event('blur', {bubbles: true}));
                    """, element)
                    
                    time.sleep(2)
                    
                    # Değer kontrolü
                    current_value = element.get_attribute('value')
                    if current_value and 'turkey' in current_value.lower():
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country input başarılı: {current_value}", "level": "success"})
                        return True
                
                # Normal input
                else:
                    element.clear()
                    element.send_keys("Turkey")
                    self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", element)
                    time.sleep(1)
                    return True
            
            # Select mu kontrol et
            elif tag_name == 'select':
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Standard select tespit edildi", "level": "debug"})
                return self.select_country_dropdown(element)
            
            # Parent container mu kontrol et
            else:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Container element, alt elementler aranıyor...", "level": "debug"})
                
                # İçinde input ara
                try:
                    search_input = element.find_element(By.CSS_SELECTOR, "input")
                    if search_input.is_displayed() and search_input.is_enabled():
                        search_input.clear()
                        search_input.send_keys("Turkey")
                        search_input.send_keys(Keys.ENTER)
                        time.sleep(2)
                        return True
                except:
                    pass
                
                # İçinde select ara
                try:
                    select_elem = element.find_element(By.CSS_SELECTOR, "select")
                    if select_elem.is_displayed() and select_elem.is_enabled():
                        return self.select_country_dropdown(select_elem)
                except:
                    pass
            
            return False
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country element doldurma hatası: {str(e)}", "level": "debug"})
            return False

    def inject_country_value(self):
        """JavaScript ile zorla country değeri enjekte et"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript country injection...", "level": "info"})
            
            success = self.driver.execute_script("""
                try {
                    console.log('Country injection başlıyor...');
                    
                    // Method 1: Direkt ID ile
                    var countryEl = document.getElementById('country');
                    if(countryEl) {
                        var input = countryEl.querySelector('input.select__search') || countryEl.querySelector('input');
                        if(input) {
                            input.value = 'Turkey';
                            input.dispatchEvent(new Event('input', {bubbles: true}));
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                            console.log('Method 1 başarılı');
                            return true;
                        }
                    }
                    
                    // Method 2: Autocomplete attribute ile
                    var autocompleteInputs = document.querySelectorAll('input[autocomplete*="country"]');
                    for(var i = 0; i < autocompleteInputs.length; i++) {
                        var input = autocompleteInputs[i];
                        if(input.offsetParent !== null) {
                            input.value = 'Turkey';
                            input.dispatchEvent(new Event('input', {bubbles: true}));
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                            console.log('Method 2 başarılı');
                            return true;
                        }
                    }
                    
                    // Method 3: Billing form item içinden
                    var billingItems = document.querySelectorAll('.billing-address-form__item--country');
                    for(var i = 0; i < billingItems.length; i++) {
                        var input = billingItems[i].querySelector('input');
                        if(input && input.offsetParent !== null) {
                            input.value = 'Turkey';
                            input.dispatchEvent(new Event('input', {bubbles: true}));
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                            console.log('Method 3 başarılı');
                            return true;
                        }
                    }
                    
                    console.log('Tüm methodlar başarısız');
                    return false;
                    
                } catch(e) {
                    console.log('JavaScript injection hatası:', e);
                    return false;
                }
            """)
            
            if success:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript injection başarılı!", "level": "success"})
                time.sleep(2)
                return True
            else:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript injection başarısız", "level": "warn"})
                return False
                
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript injection hatası: {str(e)}", "level": "debug"})
            return False

    def get_input_context(self, input_element):
        """Input'un context'ini (label, placeholder, parent text) al"""
        try:
            context_parts = []
            
            # Placeholder
            placeholder = input_element.get_attribute('placeholder') or ''
            if placeholder:
                context_parts.append(f"placeholder:{placeholder}")
            
            # Name
            name = input_element.get_attribute('name') or ''
            if name:
                context_parts.append(f"name:{name}")
            
            # ID
            input_id = input_element.get_attribute('id') or ''
            if input_id:
                context_parts.append(f"id:{input_id}")
            
            # Parent text
            try:
                parent = input_element.find_element(By.XPATH, "./parent::*")
                parent_text = parent.text[:50] if parent.text else ''
                if parent_text:
                    context_parts.append(f"parent:{parent_text}")
            except:
                pass
            
            # Label
            try:
                label = input_element.find_element(By.XPATH, "./preceding::label[1]")
                label_text = label.text[:30] if label.text else ''
                if label_text:
                    context_parts.append(f"label:{label_text}")
            except:
                pass
            
            return " | ".join(context_parts) if context_parts else "no_context"
            
        except Exception as e:
            return f"context_error:{str(e)}"

    def fill_billing_address(self):
        """Billing address bilgilerini doldur - Geliştirilmiş country debugging ile"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Billing address bilgileri giriliyor...", "level": "info"})
            time.sleep(5)  # Form elementlerinin tamamen yüklenmesi için daha uzun bekle
            
            # Önce sayfa HTML'ini debug et
            self.debug_page_structure()
            
            # Country dropdown - en kapsamlı arama
            country_filled = False
            max_country_attempts = 8  # Daha fazla deneme
            
            for attempt in range(max_country_attempts):
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country seçimi deneme {attempt + 1}/{max_country_attempts}", "level": "debug"})
                
                # JavaScript ile sayfa tarama
                country_element = self.find_country_element_js()
                if country_element:
                    if self.select_country(country_element):
                        country_filled = True
                        break
                
                # Klasik selector arama (geliştirilmiş)
                country_element = self.find_country_element_traditional()
                if country_element:
                    if self.select_country(country_element):
                        country_filled = True
                        break
                
                # Dinamik content için daha uzun bekleme
                if attempt < max_country_attempts - 1:
                    wait_time = (attempt + 1) * 3  # Artan bekleme süresi
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country bulunamadı, {wait_time} saniye bekleyip tekrar denenecek...", "level": "warn"})
                    time.sleep(wait_time)
                    
                    # Sayfayı refresh etmeden elementleri yeniden yükle
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                    self.driver.execute_script("window.scrollTo(0, 0);")
                    time.sleep(2)

            # Country seçimi kontrolü - ZORUNLU
            if not country_filled:
                # Son çare: Manuel country input denemesi
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Son çare: Manuel country girişi deneniyor...", "level": "warn"})
                if self.try_manual_country_input():
                    country_filled = True
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Manuel country girişi başarılı!", "level": "success"})
                else:
                    send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Country seçimi tamamen başarısız! Form doldurma durduruluyor."})
                    return False

            # Diğer alanları doldur (country başarılı olduktan sonra)
            time.sleep(3)  # Country seçiminin tamamlanması için bekle

            # Address
            self.fill_field_enhanced("address", self.customer_info['address'])

            # City  
            self.fill_field_enhanced("city", self.customer_info['city'])

            # Postal code
            self.fill_field_enhanced("postal", self.customer_info['postcode'])
            self.fill_field_enhanced("zip", self.customer_info['postcode'])

            # State - opsiyonel
            time.sleep(2)
            self.fill_state_field_enhanced()

            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Billing address işlemi başarıyla tamamlandı.", "level": "success"})
            return True
            
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Billing address genel hatası: {str(e)}"})
            return False

    def fill_field_enhanced(self, field_type, value):
        """Geliştirilmiş alan doldurma - Paybis form yapısına özel"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {field_type} alanı dolduriliyor: {value}", "level": "debug"})
            
            # Paybis form yapısına özel selector'lar
            selectors_map = {
                "address": [
                    # Paybis spesifik
                    "input.input[id='address']",
                    "input.input[name='address']",
                    "input[data-v-92a6c3df][id='address']",
                    ".billing-address-form__item--address input",
                    ".billing-address-form__item--address .input",
                    
                    # Genel
                    f"input[id*='{field_type}' i]",
                    f"input[name*='{field_type}' i]",
                    f"input[placeholder*='{field_type}' i]",
                    f"input[autocomplete*='{field_type}']",
                    f"textarea[placeholder*='{field_type}' i]",
                    f"textarea[name*='{field_type}' i]",
                    
                    # XPath
                    f"//label[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{field_type}')]/following::input[1]",
                    f"//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{field_type}')]//input[contains(@class, 'input')]"
                ],
                "city": [
                    # Paybis spesifik
                    "input.input[id='city']",
                    "input.input[name='city']",
                    "input[data-v-92a6c3df][id='city']",
                    ".billing-address-form__item--city input",
                    ".billing-address-form__item--city .input",
                    
                    # Genel
                    f"input[id*='{field_type}' i]",
                    f"input[name*='{field_type}' i]",
                    f"input[placeholder*='{field_type}' i]",
                    f"input[autocomplete*='address-level2']",
                    
                    # XPath
                    f"//label[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{field_type}')]/following::input[1]",
                    f"//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{field_type}')]//input[contains(@class, 'input')]"
                ],
                "postal": [
                    # Paybis spesifik - ZIP
                    "input.input[id='zip']",
                    "input.input[name='zip']",
                    "input[data-v-92a6c3df][id='zip']",
                    ".billing-address-form__item--zip input",
                    ".billing-address-form__item--zip .input",
                    
                    # Postal variations
                    f"input[id*='postal' i]",
                    f"input[name*='postal' i]",
                    f"input[placeholder*='postal' i]",
                    f"input[autocomplete*='postal-code']",
                    
                    # Zip variations
                    f"input[id*='zip' i]",
                    f"input[name*='zip' i]",
                    f"input[placeholder*='zip' i]",
                    
                    # XPath
                    "//label[contains(text(), 'Postal') or contains(text(), 'Zip')]/following::input[1]",
                    "//div[contains(text(), 'Postal') or contains(text(), 'Zip')]//input[contains(@class, 'input')]"
                ],
                "zip": [
                    # Paybis spesifik
                    "input.input[id='zip']",
                    "input.input[name='zip']",
                    "input[data-v-92a6c3df][id='zip']",
                    ".billing-address-form__item--zip input",
                    
                    f"input[id*='zip' i]",
                    f"input[name*='zip' i]",
                    f"input[placeholder*='zip' i]"
                ]
            }
            
            selectors = selectors_map.get(field_type, [])
            
            for selector in selectors:
                try:
                    elements = []
                    if selector.startswith("//"):
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            # Element'e scroll
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                            time.sleep(0.5)
                            
                            # Value clear ve set
                            element.clear()
                            element.send_keys(value)
                            
                            # Events trigger
                            self.driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles: true}));", element)
                            self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", element)
                            
                            # Değer kontrolü
                            current_value = element.get_attribute('value')
                            if current_value == value:
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {field_type} başarıyla girildi: {value}", "level": "success"})
                                return True
                            else:
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {field_type} değer kontrolü başarısız: Expected {value}, Got {current_value}", "level": "debug"})
                            
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {field_type} selector hatası {selector}: {str(e)}", "level": "debug"})
                    continue
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {field_type} doldurma başarısız!", "level": "warn"})
            return False
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {field_type} doldurma hatası: {str(e)}", "level": "debug"})
            return False

    def fill_state_field_enhanced(self):
        """Geliştirilmiş state doldurma - Paybis form yapısına özel"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State alanı aranıyor...", "level": "debug"})
            
            # Paybis state field spesifik selector'lar
            state_selectors = [
                # Paybis spesifik - custom dropdown
                "div.select[id='state']:not(.select--disabled)",
                ".billing-address-form__item--state .select:not(.select--disabled)",
                ".billing-address-form__item--state div[id='state']",
                
                # Search input (state enabled olduktan sonra)
                ".billing-address-form__item--state input.select__search:not([disabled])",
                "input.select__search[autocomplete*='address-level1']:not([disabled])",
                
                # Standard selectors
                "select[name*='state' i]:not([disabled])",
                "select[id*='state' i]:not([disabled])",
                "select[name*='province' i]:not([disabled])",
                "input[name*='state' i]:not([disabled])",
                "input[placeholder*='state' i]:not([disabled])",
                
                # XPath
                "//label[contains(text(), 'State')]/following::div[contains(@class, 'select') and not(contains(@class, 'disabled'))][1]",
                "//label[contains(text(), 'State')]/following::select[not(@disabled)][1]",
                "//label[contains(text(), 'State')]/following::input[not(@disabled)][1]"
            ]
            
            for selector in state_selectors:
                try:
                    elements = []
                    if selector.startswith("//"):
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            element_class = element.get_attribute('class') or ''
                            
                            # Custom dropdown mu kontrol et
                            if 'select' in element_class and element.tag_name.lower() == 'div':
                                # Custom state dropdown
                                try:
                                    # Dropdown'ı aç
                                    element.click()
                                    time.sleep(2)
                                    
                                    # Search input'a Istanbul yaz
                                    search_input = element.find_element(By.CSS_SELECTOR, "input.select__search")
                                    if search_input and search_input.is_enabled():
                                        search_input.clear()
                                        search_input.send_keys("Istanbul")
                                        search_input.send_keys(Keys.ENTER)
                                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State custom dropdown - Istanbul girildi", "level": "success"})
                                        return True
                                        
                                except Exception as custom_error:
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Custom state dropdown hatası: {str(custom_error)}", "level": "debug"})
                                    continue
                                    
                            elif element.tag_name.lower() == 'select':
                                # Standard select
                                try:
                                    select_obj = Select(element)
                                    if len(select_obj.options) > 1:
                                        # İstanbul ara
                                        for option in select_obj.options:
                                            if 'istanbul' in option.text.lower():
                                                select_obj.select_by_visible_text(option.text)
                                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State seçildi: {option.text}", "level": "success"})
                                                return True
                                        
                                        # İlk seçenek
                                        select_obj.select_by_index(1)
                                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State ilk seçenekle dolduruldu", "level": "info"})
                                        return True
                                except Exception as select_error:
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Standard state select hatası: {str(select_error)}", "level": "debug"})
                                    continue
                                    
                            else:
                                # Input field
                                try:
                                    element.clear()
                                    element.send_keys("Istanbul")
                                    self.driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles: true}));", element)
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State input'a girildi: Istanbul", "level": "success"})
                                    return True
                                except Exception as input_error:
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State input hatası: {str(input_error)}", "level": "debug"})
                                    continue
                            
                            time.sleep(1)
                            break
                    break
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State selector hatası {selector}: {str(e)}", "level": "debug"})
                    continue
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State alanı bulunamadı veya disabled", "level": "info"})
            return False
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] State doldurma hatası: {str(e)}", "level": "debug"})
            return False

    def complete_payment(self):
        """Son adım: Pay butonuna tıkla - form validasyonu ile"""
        send_to_node("progress", {"progress": 90, "step": "Ödeme tamamlanıyor..."})
        try:
            time.sleep(3)  # Form completion için bekle
            
            # Form validasyonu - zorunlu alanları kontrol et
            if not self.validate_form_before_payment():
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Form validasyonu başarısız!"})
                return False
            
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Pay butonu aranıyor...", "level": "debug"})
            
            # Pay butonunu bul - HTML'deki gerçek selector'lar
            pay_selectors = [
                # Paybis spesifik (HTML'den)
                "button[data-testid='pay-button']",
                "button.btn.btn-lg.btn-primary:not([disabled])",
                ".sales-funnel-actions__submit button",
                
                # Genel selector'lar
                "//button[contains(text(), 'Pay') or contains(text(), 'PAY')]",
                "//button[contains(text(), 'Continue') and not(contains(text(), 'Back'))]",
                "//button[contains(text(), 'Submit')]",
                "//button[contains(text(), 'Complete')]",
                "//button[@type='submit']",
                "//input[@type='submit']",
                "button[data-testid*='pay']",
                "button[data-testid*='submit']",
                ".pay-button",
                ".submit-button",
                ".complete-button",
                "form button[type='submit']",
                "form button:last-of-type",
                "//form//button[last()]"
            ]
            
            pay_button = None
            for selector in pay_selectors:
                try:
                    if selector.startswith("//"):
                        pay_elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        pay_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in pay_elements:
                        if element.is_displayed() and element.is_enabled():
                            # Button text'ini kontrol et
                            button_text = element.text.lower()
                            if any(keyword in button_text for keyword in ['pay', 'submit', 'complete', 'continue', 'next']) and 'back' not in button_text and 'cancel' not in button_text:
                                pay_button = element
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Pay butonu bulundu: {selector} - Text: '{element.text}'", "level": "debug"})
                                break
                    
                    if pay_button:
                        break
                        
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Pay selector hatası {selector}: {str(e)}", "level": "debug"})
                    continue
            
            # Eğer bulunamazsa, form'daki en son button'u dene
            if not pay_button:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Spesifik pay button bulunamadı, form button'ları aranıyor...", "level": "debug"})
                try:
                    form_buttons = self.driver.find_elements(By.CSS_SELECTOR, "form button, .form button, .card-form button")
                    for button in form_buttons:
                        if button.is_displayed() and button.is_enabled() and 'cancel' not in button.text.lower() and 'back' not in button.text.lower():
                            pay_button = button
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Form button bulundu: '{button.text}'", "level": "debug"})
                            break
                except Exception as e:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Form button arama hatası: {str(e)}", "level": "debug"})
            
            if pay_button:
                # Scroll to button
                self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", pay_button)
                time.sleep(2)
                
                # Button enable olmasını bekle
                try:
                    WebDriverWait(self.driver, 10).until(
                        lambda driver: pay_button.is_enabled() and pay_button.is_displayed()
                    )
                except:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Pay button enable bekleme timeout'u", "level": "warn"})
                
                # Click pay button
                try:
                    pay_button.click()
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Pay butonuna tıklandı (normal click).", "level": "info"})
                except Exception as normal_click_error:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Normal click hatası: {str(normal_click_error)}", "level": "debug"})
                    try:
                        # JavaScript click
                        self.driver.execute_script("arguments[0].click();", pay_button)
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Pay butonuna tıklandı (JavaScript click).", "level": "info"})
                    except Exception as js_click_error:
                        send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Pay button click başarısız: {str(js_click_error)}"})
                        return False
                
                time.sleep(5)  # Payment processing için bekle
                
                # 3DS veya başka doğrulama kontrolü
                self.handle_additional_verification()
                
                return True
            else:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Pay butonu hiç bulunamadı!"})
                
                # Debug için sayfa bilgilerini al
                try:
                    current_url = self.driver.current_url
                    page_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                    button_texts = [btn.text for btn in page_buttons if btn.is_displayed()]
                    
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Debug - URL: {current_url}", "level": "debug"})
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Debug - Mevcut button'lar: {button_texts}", "level": "debug"})
                except:
                    pass
                
                return False
                
        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Payment tamamlama hatası: {str(e)}"})
            return False

    def validate_form_before_payment(self):
        """Pay butonuna tıklamadan önce form validasyonu"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Form validasyonu yapılıyor...", "level": "info"})
            
            # Zorunlu alanları kontrol et
            validation_checks = []
            
            # Country seçimi kontrol - Custom dropdown desteği ile
            country_selected = False
            try:
                # Önce standard select'leri kontrol et
                country_selects = self.driver.find_elements(By.CSS_SELECTOR, "select")
                for select_elem in country_selects:
                    if select_elem.is_displayed():
                        select_obj = Select(select_elem)
                        selected_value = select_obj.first_selected_option.get_attribute('value')
                        selected_text = select_obj.first_selected_option.text
                        
                        if selected_value and selected_value != '' and selected_text.lower() not in ['select', 'choose', 'country']:
                            country_selected = True
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country seçimi bulundu (select): {selected_text}", "level": "info"})
                            break
                
                # Custom dropdown kontrolü (Paybis için)
                if not country_selected:
                    custom_country_selectors = [
                        "div.select[id='country'] input.select__search",
                        ".billing-address-form__item--country input.select__search",
                        "input.select__search[autocomplete*='country']"
                    ]
                    
                    for selector in custom_country_selectors:
                        try:
                            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                            for elem in elements:
                                if elem.is_displayed():
                                    value = elem.get_attribute('value') or ''
                                    if value.strip() and value.lower() not in ['', 'select', 'choose']:
                                        country_selected = True
                                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country seçimi bulundu (custom): {value}", "level": "info"})
                                        break
                            if country_selected:
                                break
                        except:
                            continue
                            
            except Exception as country_check_error:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country kontrol hatası: {str(country_check_error)}", "level": "debug"})
            
            validation_checks.append(("Country", country_selected))
            
            # Diğer zorunlu alanları kontrol et
            required_fields = {
                "Card Number": ["input[autocomplete='cc-number']", "input[placeholder*='card number' i]", "input[name='number']"],
                "Expiry": ["input[autocomplete='cc-exp']", "input[placeholder*='expiry' i]", "input[placeholder*='mm/yy' i]", "input[name='expiry']"],
                "CVV": ["input[autocomplete='cc-csc']", "input[placeholder*='cvv' i]", "input[placeholder*='cvc' i]", "input[name='cvv']"],
                "Address": ["input[placeholder*='address' i]", "input[name*='address' i]", "input[id='address']"],
                "City": ["input[placeholder*='city' i]", "input[name*='city' i]", "input[id='city']"],
                "Postal": ["input[placeholder*='postal' i]", "input[placeholder*='zip' i]", "input[id='zip']"]
            }
            
            for field_name, selectors in required_fields.items():
                field_filled = False
                for selector in selectors:
                    try:
                        # Ana sayfada ara
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        for elem in elements:
                            if elem.is_displayed() and elem.get_attribute('value').strip():
                                field_filled = True
                                break
                        
                        # iframe içinde ara (kart bilgileri için)
                        if not field_filled and field_name in ["Card Number", "Expiry", "CVV"]:
                            try:
                                iframes = self.driver.find_elements(By.CSS_SELECTOR, "iframe")
                                for iframe in iframes:
                                    if iframe.is_displayed():
                                        self.driver.switch_to.frame(iframe)
                                        iframe_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                                        for iframe_elem in iframe_elements:
                                            if iframe_elem.is_displayed() and iframe_elem.get_attribute('value').strip():
                                                field_filled = True
                                                break
                                        self.driver.switch_to.default_content()
                                        if field_filled:
                                            break
                            except:
                                self.driver.switch_to.default_content()
                        
                        if field_filled:
                            break
                    except:
                        continue
                
                validation_checks.append((field_name, field_filled))
            
            # Sonuçları logla
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Form validasyon sonuçları:", "level": "info"})
            for field_name, is_filled in validation_checks:
                status = "✓" if is_filled else "✗"
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] {status} {field_name}: {'OK' if is_filled else 'MISSING'}", "level": "info"})
            
            # Country zorunlu - diğerleri opsiyonel warning
            if not country_selected:
                send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Country seçimi zorunlu!"})
                return False
            
            missing_fields = [name for name, filled in validation_checks if not filled]
            if missing_fields:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Eksik alanlar (devam ediyor): {', '.join(missing_fields)}", "level": "warn"})
            
            return True
            
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Form validasyon hatası: {str(e)}", "level": "warn"})
            return True  # Validasyon hatası olsa bile devam et

    def handle_additional_verification(self):
        """3DS veya ek doğrulama işlemleri"""
        try:
            time.sleep(5)
            current_url = self.driver.current_url
            page_content = self.driver.page_source.lower()
            
            if "3ds" in current_url.lower() or "secure" in current_url.lower() or "verification" in page_content:
                send_to_node("verification_required", {
                    "gatewayName": "Paybis",
                    "type": "3ds",
                    "timeLimit": 300,
                    "message": "3D Secure verification required. Please complete the bank authentication."
                })

                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 3D Secure bekleniyor...", "level": "info"})
                
                # Kullanıcının 3DS'i tamamlamasını bekle
                code_from_node_json = sys.stdin.readline()
                code_payload = json.loads(code_from_node_json)
                
                if code_payload.get("type") == "verification_code":
                    time.sleep(5)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 3D Secure tamamlandı.", "level": "info"})
                    
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Ek doğrulama kontrolü hatası: {str(e)}", "level": "warn"})
def debug_page_comprehensive(self):
    """Comprehensive page debugging with visual element mapping"""
    try:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Starting comprehensive page debug...", "level": "info"})
        
        # Basic page info
        page_info = self.driver.execute_script("""
            return {
                url: window.location.href,
                title: document.title,
                readyState: document.readyState,
                forms: document.querySelectorAll('form').length,
                inputs: document.querySelectorAll('input').length,
                selects: document.querySelectorAll('select').length,
                customSelects: document.querySelectorAll('[class*="select"]:not(select)').length,
                iframes: document.querySelectorAll('iframe').length,
                scripts: document.querySelectorAll('script').length,
                reactComponents: !!window.React || !!document.querySelector('[data-react]'),
                vueComponents: !!window.Vue || !!document.querySelector('[data-v-]'),
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight,
                scrollY: window.scrollY,
                documentHeight: document.documentElement.scrollHeight
            };
        """)
        
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Page Info: {page_info}", "level": "debug"})
        
        # Analyze all forms and their fields
        forms_data = self.driver.execute_script("""
            const forms = document.querySelectorAll('form, [class*="form"], [class*="checkout"], [class*="billing"]');
            const formData = [];
            
            forms.forEach((form, index) => {
                const rect = form.getBoundingClientRect();
                const inputs = form.querySelectorAll('input, select, textarea, [class*="select"]:not(select)');
                const fieldData = [];
                
                inputs.forEach((input, inputIndex) => {
                    const inputRect = input.getBoundingClientRect();
                    const isVisible = inputRect.width > 0 && inputRect.height > 0 && 
                                    input.offsetParent !== null;
                    
                    fieldData.push({
                        index: inputIndex,
                        tagName: input.tagName,
                        type: input.type || 'unknown',
                        id: input.id || '',
                        name: input.name || '',
                        className: input.className || '',
                        placeholder: input.placeholder || '',
                        autocomplete: input.autocomplete || '',
                        value: input.value || '',
                        visible: isVisible,
                        enabled: !input.disabled,
                        required: input.required,
                        width: Math.round(inputRect.width),
                        height: Math.round(inputRect.height),
                        x: Math.round(inputRect.x),
                        y: Math.round(inputRect.y)
                    });
                });
                
                formData.push({
                    index: index,
                    id: form.id || '',
                    className: form.className || '',
                    action: form.action || '',
                    method: form.method || '',
                    visible: rect.width > 0 && rect.height > 0,
                    fieldCount: inputs.length,
                    visibleFieldCount: fieldData.filter(f => f.visible).length,
                    fields: fieldData
                });
            });
            
            return formData;
        """)
        
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Found {len(forms_data)} forms", "level": "info"})
        
        for i, form in enumerate(forms_data):
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Form {i+1}: {form['fieldCount']} fields ({form['visibleFieldCount']} visible)", "level": "debug"})
            
            # Look for potential country fields
            for field in form['fields']:
                if field['visible'] and self.is_likely_country_field(field):
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 🎯 POTENTIAL COUNTRY FIELD: {field}", "level": "info"})
        
        # Create visual element map
        self.create_visual_element_map()
        
        # Check for dynamic content loading
        self.check_dynamic_content_indicators()
        
        return forms_data
        
    except Exception as e:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Comprehensive debug error: {str(e)}", "level": "debug"})
        return []

def is_likely_country_field(self, field_data):
    """Analyze if a field is likely to be a country selector"""
    try:
        # Text-based indicators
        text_indicators = [
            'country', 'billing country', 'pais', 'land', 'nationality',
            'shipping country', 'address country'
        ]
        
        # Check all text fields
        searchable_text = ' '.join([
            field_data.get('id', '').lower(),
            field_data.get('name', '').lower(),
            field_data.get('className', '').lower(),
            field_data.get('placeholder', '').lower(),
            field_data.get('autocomplete', '').lower()
        ])
        
        # Strong indicators
        strong_matches = any(indicator in searchable_text for indicator in text_indicators)
        
        # Weak indicators (position, type, etc.)
        weak_indicators = []
        
        # Custom dropdown patterns
        if 'select' in field_data.get('className', '') and field_data.get('tagName') == 'DIV':
            weak_indicators.append('custom_dropdown')
        
        # First select in form (often country)
        if field_data.get('tagName') == 'SELECT' and field_data.get('index', 0) == 0:
            weak_indicators.append('first_select')
        
        # Autocomplete hints
        if 'country' in field_data.get('autocomplete', ''):
            weak_indicators.append('autocomplete_hint')
        
        # Position-based (top-left area)
        if field_data.get('y', 999) < 300 and field_data.get('x', 999) < 400:
            weak_indicators.append('top_position')
        
        confidence_score = 0
        if strong_matches:
            confidence_score += 10
        confidence_score += len(weak_indicators) * 2
        
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Field analysis - Strong: {strong_matches}, Weak: {weak_indicators}, Score: {confidence_score}", "level": "debug"})
        
        return confidence_score >= 6  # Threshold for likely country field
        
    except Exception as e:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Country field analysis error: {str(e)}", "level": "debug"})
        return False

def create_visual_element_map(self):
    """Create a visual map of page elements for debugging"""
    try:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Creating visual element map...", "level": "debug"})
        
        # Take screenshot for visual reference
        screenshot_data = self.driver.get_screenshot_as_base64()
        
        # Get element positions and overlay information
        element_map = self.driver.execute_script("""
            const elements = document.querySelectorAll('input, select, [class*="select"]:not(select), button');
            const elementInfo = [];
            
            elements.forEach((elem, index) => {
                const rect = elem.getBoundingClientRect();
                if (rect.width > 10 && rect.height > 10 && elem.offsetParent !== null) {
                    
                    // Get context information
                    const parentText = elem.closest('label, .field, .form-group, [class*="item"]');
                    const contextText = parentText ? parentText.textContent.slice(0, 50) : '';
                    
                    elementInfo.push({
                        index: index,
                        tagName: elem.tagName,
                        id: elem.id || '',
                        className: elem.className || '',
                        type: elem.type || '',
                        placeholder: elem.placeholder || '',
                        value: elem.value || '',
                        text: elem.textContent.slice(0, 30),
                        context: contextText,
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        visible: true,
                        zIndex: window.getComputedStyle(elem).zIndex || 'auto'
                    });
                }
            });
            
            return elementInfo;
        """)
        
        # Log interesting elements
        country_candidates = []
        for elem in element_map:
            if any(keyword in elem.get('context', '').lower() for keyword in ['country', 'billing', 'address']):
                country_candidates.append(elem)
        
        if country_candidates:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Visual country candidates: {len(country_candidates)}", "level": "info"})
            for candidate in country_candidates:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 📍 Candidate at ({candidate['x']}, {candidate['y']}): {candidate['context'][:30]}", "level": "debug"})
        
        return element_map
        
    except Exception as e:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Visual element map error: {str(e)}", "level": "debug"})
        return []

def check_dynamic_content_indicators(self):
    """Check for indicators that content is still loading dynamically"""
    try:
        dynamic_indicators = self.driver.execute_script("""
            return {
                hasSpinners: document.querySelectorAll('[class*="spin"], [class*="load"], [class*="wait"]').length > 0,
                hasPlaceholders: document.querySelectorAll('[class*="placeholder"], [class*="skeleton"]').length > 0,
                hasAsyncScripts: Array.from(document.querySelectorAll('script')).some(s => s.async || s.defer),
                hasReactSuspense: !!document.querySelector('[data-react-suspense]'),
                hasLazyLoading: document.querySelectorAll('[loading="lazy"]').length > 0,
                pendingFetches: window.fetch ? (window.fetch.pending || 0) : 0,
                documentReadyState: document.readyState,
                hasIntersectionObserver: !!window.IntersectionObserver,
                activeMutationObservers: window.MutationObserver ? 'unknown' : 'not_supported',
                networkState: navigator.onLine ? 'online' : 'offline',
                performanceTiming: {
                    domLoading: performance.timing.domLoading,
                    domComplete: performance.timing.domComplete,
                    loadEventEnd: performance.timing.loadEventEnd
                }
            };
        """)
        
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Dynamic content indicators: {dynamic_indicators}", "level": "debug"})
        
        # Determine if we should wait longer
        should_wait_longer = (
            dynamic_indicators.get('hasSpinners', False) or
            dynamic_indicators.get('hasPlaceholders', False) or
            dynamic_indicators.get('documentReadyState') != 'complete'
        )
        
        if should_wait_longer:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ⏳ Dynamic content still loading, recommend waiting", "level": "warn"})
        else:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ✅ Page appears fully loaded", "level": "info"})
        
        return should_wait_longer
        
    except Exception as e:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Dynamic content check error: {str(e)}", "level": "debug"})
        return False

def emergency_country_fallback(self):
    """Emergency fallback when all other methods fail"""
    try:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 🚨 EMERGENCY COUNTRY FALLBACK ACTIVATED", "level": "warn"})
        
        # Method 1: Brute force - try clicking everything that might be a dropdown
        potential_dropdowns = self.driver.find_elements(By.CSS_SELECTOR, 
            "select, [class*='select'], [role='combobox'], [role='listbox'], .dropdown, [class*='dropdown']")
        
        for i, dropdown in enumerate(potential_dropdowns[:10]):  # Limit to first 10
            try:
                if dropdown.is_displayed() and dropdown.is_enabled():
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Emergency attempt {i+1}: Trying dropdown", "level": "debug"})
                    
                    # Try to interact
                    dropdown.click()
                    time.sleep(1)
                    
                    # Look for Turkey option
                    turkey_options = self.driver.find_elements(By.XPATH, 
                        "//*[contains(text(), 'Turkey') or contains(text(), 'TURKEY') or @value='TR']")
                    
                    for option in turkey_options:
                        if option.is_displayed():
                            option.click()
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 🎯 Emergency Turkey selection successful!", "level": "success"})
                            return True
                    
                    # Try typing Turkey if no options found
                    search_inputs = dropdown.find_elements(By.CSS_SELECTOR, "input")
                    for search_input in search_inputs:
                        if search_input.is_displayed() and search_input.is_enabled():
                            search_input.clear()
                            search_input.send_keys("Turkey")
                            search_input.send_keys(Keys.ENTER)
                            
                            # Check if it worked
                            time.sleep(1)
                            if "turkey" in search_input.get_attribute('value').lower():
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 🎯 Emergency typing successful!", "level": "success"})
                                return True
                    
                    # Close dropdown if it's still open
                    try:
                        dropdown.send_keys(Keys.ESCAPE)
                    except:
                        pass
                        
            except Exception as dropdown_error:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Emergency dropdown {i+1} error: {str(dropdown_error)}", "level": "debug"})
                continue
        
        # Method 2: JavaScript injection - force set country value
        success = self.driver.execute_script("""
            try {
                console.log('Emergency JS country injection...');
                
                // Try to find and set any country-related fields
                const countryFields = [
                    ...document.querySelectorAll('[id*="country" i], [name*="country" i]'),
                    ...document.querySelectorAll('select'),
                    ...document.querySelectorAll('input[type="text"]'),
                    ...document.querySelectorAll('[class*="select"]')
                ];
                
                for (const field of countryFields) {
                    if (field.offsetParent !== null) {
                        // Try different approaches
                        if (field.tagName === 'SELECT') {
                            // Standard select
                            for (let i = 0; i < field.options.length; i++) {
                                const option = field.options[i];
                                if (option.text.toLowerCase().includes('turkey') || 
                                    option.value.toLowerCase().includes('tr')) {
                                    field.selectedIndex = i;
                                    field.dispatchEvent(new Event('change', {bubbles: true}));
                                    console.log('Emergency select Turkey success');
                                    return true;
                                }
                            }
                        } else if (field.tagName === 'INPUT') {
                            // Input field
                            field.value = 'Turkey';
                            field.dispatchEvent(new Event('input', {bubbles: true}));
                            field.dispatchEvent(new Event('change', {bubbles: true}));
                            console.log('Emergency input Turkey success');
                            return true;
                        } else {
                            // Custom dropdown
                            const searchInput = field.querySelector('input');
                            if (searchInput) {
                                searchInput.value = 'Turkey';
                                searchInput.dispatchEvent(new Event('input', {bubbles: true}));
                                searchInput.dispatchEvent(new Event('change', {bubbles: true}));
                                console.log('Emergency custom dropdown Turkey success');
                                return true;
                            }
                        }
                    }
                }
                
                console.log('Emergency injection failed');
                return false;
                
            } catch (e) {
                console.log('Emergency injection error:', e);
                return false;
            }
        """)
        
        if success:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 🎯 Emergency JavaScript injection successful!", "level": "success"})
            return True
        
        # Method 3: User notification for manual intervention
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] 🆘 All emergency methods failed - manual intervention required", "level": "error"})
        
        return False
        
    except Exception as e:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Emergency fallback error: {str(e)}", "level": "error"})
        return False

def smart_wait_for_element(self, selectors, timeout=30, check_interval=0.5):
    """Smart element waiting with multiple strategies"""
    try:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Smart wait starting for {len(selectors)} selectors", "level": "debug"})
        
        start_time = time.time()
        last_page_state = None
        
        while time.time() - start_time < timeout:
            # Check current page state
            current_page_state = self.driver.execute_script("""
                return {
                    url: window.location.href,
                    title: document.title,
                    readyState: document.readyState,
                    elementCount: document.querySelectorAll('*').length,
                    formCount: document.querySelectorAll('form').length,
                    inputCount: document.querySelectorAll('input').length
                };
            """)
            
            # Check if page changed significantly
            if last_page_state and self.page_changed_significantly(last_page_state, current_page_state):
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Page state changed significantly", "level": "debug"})
            
            last_page_state = current_page_state
            
            # Try each selector
            for selector_data in selectors:
                if isinstance(selector_data, str):
                    selector = selector_data
                    by_method = By.CSS_SELECTOR
                else:
                    selector = selector_data.get('selector', '')
                    by_method = selector_data.get('by', By.CSS_SELECTOR)
                
                try:
                    if by_method == By.XPATH:
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Smart wait found element: {selector}", "level": "success"})
                            return element
                            
                except Exception as selector_error:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Selector error {selector}: {str(selector_error)}", "level": "debug"})
                    continue
            
            # Dynamic wait strategies
            if time.time() - start_time > timeout / 3:  # After 1/3 of timeout
                # Try scrolling to trigger lazy loading
                self.driver.execute_script("window.scrollBy(0, 100);")
            
            if time.time() - start_time > timeout * 2/3:  # After 2/3 of timeout
                # Try clicking on form areas to activate them
                try:
                    form_areas = self.driver.find_elements(By.CSS_SELECTOR, "form, .form, [class*='form']")
                    if form_areas:
                        form_areas[0].click()
                except:
                    pass
            
            time.sleep(check_interval)
        
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Smart wait timeout after {timeout}s", "level": "warn"})
        return None
        
    except Exception as e:
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Smart wait error: {str(e)}", "level": "debug"})
        return None

def page_changed_significantly(self, old_state, new_state):
    """Check if page state changed significantly"""
    try:
        # URL change
        if old_state['url'] != new_state['url']:
            return True
        
        # Large change in element count (indicates dynamic loading)
        element_change = abs(new_state['elementCount'] - old_state['elementCount'])
        if element_change > 10:
            return True
        
        # Form structure change
        if old_state['formCount'] != new_state['formCount']:
            return True
        
        # Input count change
        input_change = abs(new_state['inputCount'] - old_state['inputCount'])
        if input_change > 3:
            return True
        
        return False
        
    except Exception as e:
        return False
    def close_intro_popup(self):
        """Intro.js tutorial popup'ını kapat"""
        try:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Intro popup kontrol ediliyor...", "level": "debug"})
            
            # Intro.js popup var mı kontrol et
            popup_selectors = [
                ".introjs-tooltip",
                ".introjs-overlay",
                "[class*='intro']",
                ".tutorial-popup",
                ".guide-popup"
            ]
            
            popup_found = False
            for selector in popup_selectors:
                try:
                    popup_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if popup_elements and any(elem.is_displayed() for elem in popup_elements):
                        popup_found = True
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Intro popup bulundu: {selector}", "level": "debug"})
                        break
                except:
                    continue
            
            if popup_found:
                # Popup'ı kapatma yöntemleri
                close_selectors = [
                    ".introjs-skipbutton",           # Skip/Close button (×)
                    ".introjs-nextbutton.introjs-donebutton", # OK Done button (combination)
                    ".introjs-donebutton",           # Done button
                    ".introjs-button.introjs-nextbutton",  # OK/Next button
                    ".introjs-nextbutton",           # Next button
                    "//a[contains(@class, 'introjs-skipbutton')]",  # XPath skip
                    "//a[text()='×']",               # × text ile
                    "//a[contains(@class, 'introjs-donebutton') and text()='OK']",  # OK Done button
                    "//a[contains(@class, 'introjs-nextbutton') and text()='OK']",  # OK button
                    ".introjs-tooltipbuttons a:last-child",  # Son button (genelde OK)
                    ".introjs-tooltipbuttons a",     # Herhangi bir button
                ]
                
                for selector in close_selectors:
                    try:
                        if selector.startswith("//"):
                            close_buttons = self.driver.find_elements(By.XPATH, selector)
                        else:
                            close_buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        for close_button in close_buttons:
                            if close_button.is_displayed() and close_button.is_enabled():
                                # Scroll to button first
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", close_button)
                                time.sleep(0.5)
                                
                                # Try normal click first
                                try:
                                    close_button.click()
                                except:
                                    # Try JavaScript click
                                    self.driver.execute_script("arguments[0].click();", close_button)
                                
                                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Intro popup kapatıldı: {selector}", "level": "info"})
                                time.sleep(2)  # Popup'ın tamamen kapanması için bekle
                                
                                # Popup kapandı mı kontrol et
                                remaining_popups = self.driver.find_elements(By.CSS_SELECTOR, ".introjs-tooltip:not([style*='display: none'])")
                                if not remaining_popups:
                                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Popup başarıyla kapandı", "level": "success"})
                                    return True
                                
                    except Exception as e:
                        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Close button hatası {selector}: {str(e)}", "level": "debug"})
                        continue
                
                # Eğer button'lar çalışmazsa ESC tuşu dene
                try:
                    self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] ESC tuşu ile popup kapatılmaya çalışıldı", "level": "debug"})
                    time.sleep(1)
                except:
                    pass
                
                # JavaScript ile zorla kapat
                try:
                    self.driver.execute_script("""
                        // Intro.js popup'larını zorla kapat
                        console.log('Trying to close intro popups...');
                        
                        // Önce button'lara tıklamayı dene
                        var skipButton = document.querySelector('.introjs-skipbutton');
                        var doneButton = document.querySelector('.introjs-donebutton');
                        var nextButton = document.querySelector('.introjs-nextbutton');
                        
                        if (skipButton && skipButton.offsetParent !== null) {
                            console.log('Clicking skip button');
                            skipButton.click();
                        } else if (doneButton && doneButton.offsetParent !== null) {
                            console.log('Clicking done button');
                            doneButton.click();
                        } else if (nextButton && nextButton.offsetParent !== null) {
                            console.log('Clicking next button');
                            nextButton.click();
                        }
                        
                        // Element'leri gizle
                        var introElements = document.querySelectorAll('.introjs-tooltip, .introjs-overlay, [class*="intro"]');
                        introElements.forEach(function(elem) { 
                            if (elem.style) {
                                elem.style.display = 'none';
                                elem.style.opacity = '0';
                                elem.style.visibility = 'hidden';
                            }
                        });
                        
                        // Intro.js exit fonksiyonu varsa çağır
                        if (window.introJs && typeof window.introJs().exit === 'function') {
                            console.log('Calling introJs().exit()');
                            window.introJs().exit();
                        }
                        
                        // Global intro instance varsa kapat
                        if (window.intro && typeof window.intro.exit === 'function') {
                            console.log('Calling window.intro.exit()');
                            window.intro.exit();
                        }
                        
                        console.log('Intro popup cleanup completed');
                    """)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript ile popup kapatıldı", "level": "info"})
                    time.sleep(2)
                except Exception as js_error:
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] JavaScript popup kapatma hatası: {str(js_error)}", "level": "warn"})
            else:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Intro popup bulunamadı", "level": "debug"})
            
            # Son kontrol: popup hala var mı?
            time.sleep(1)
            remaining_popups = self.driver.find_elements(By.CSS_SELECTOR, ".introjs-tooltip[style*='display: block'], .introjs-tooltip:not([style*='display: none'])")
            if remaining_popups:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Hala {len(remaining_popups)} popup var, tekrar kapatılmaya çalışılıyor...", "level": "warn"})
                # Tekrar JavaScript dene
                try:
                    self.driver.execute_script("""
                        document.querySelectorAll('.introjs-tooltip, .introjs-overlay').forEach(function(elem) {
                            elem.remove();
                        });
                    """)
                    send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Popup element'leri DOM'dan silindi", "level": "info"})
                except:
                    pass
                
        except Exception as e:
            send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Intro popup kapatma hatası: {str(e)}", "level": "warn"})
            # Hata olsa da devam et
    
    def start(self):
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Ana süreç başlatılıyor...", "level": "info"})
        try:
            # Adım 1: Ana form - miktar ve currency seçimi
            if not self.initialize_purchase(): 
                return False
            
            # Adım 2: Email girişi
            if not self.enter_email(): 
                return False
                
            # Adım 3: Email OTP doğrulama
            if not self.verify_email_otp(): 
                return False
                
            # Adım 4: Wallet seçimi
            if not self.select_wallet(): 
                return False
                
            # Adım 5: New card seçimi
            if not self.select_new_card(): 
                return False
                
            # Adım 6: Kart bilgileri
            if not self.fill_card_details(): 
                return False
                
            # Adım 7: Payment completion
            if not self.complete_payment(): 
                return False

            # Success
            send_to_node("progress", {"progress": 100, "step": "Ödeme tamamlandı!"})
            send_to_node("success", {
                "gatewayName": "Paybis",
                "orderNumber": f"PAYBIS-ORD-{self.order_id}",
                "transactionId": f"PAYBIS-TXN-{int(time.time())}",
                "cryptoCurrency": "BTC",
                "cryptoAmount": f"{float(self.amount_eur) / 65000:.8f}",
                "message": "Paybis payment completed successfully."
            })
            return True

        except Exception as e:
            send_to_node("error", {"message": f"[PaybisBot:{self.order_id}] Ana süreç hatası: {str(e)} - {traceback.format_exc()}"})
            return False
        finally:
            self.cleanup()

    def cleanup(self):
        """Temizlik işlemi"""
        send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Temizlik işlemi başlatılıyor.", "level": "info"})
        
        if self.driver:
            try:
                self.driver.quit()
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Chrome driver kapatıldı.", "level": "debug"})
            except Exception as e:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Driver kapatma hatası: {str(e)}", "level": "warn"})
            finally:
                self.driver = None
        
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Temp directory temizlendi.", "level": "debug"})
            except Exception as e:
                send_to_node("log", {"message": f"[PaybisBot:{self.order_id}] Temp directory temizleme hatası: {str(e)}", "level": "warn"})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paybis Payment Bot")
    
    parser.add_argument("--json", action="store_true", help="Enable JSON communication mode")
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--amount", required=True, dest="amount_eur", type=str)
    parser.add_argument("--wallet", required=True, dest="wallet_address")
    parser.add_argument("--card-number", required=True)
    parser.add_argument("--card-holder", help="Full name of the card holder.")
    parser.add_argument("--card-expiry", required=True, help="MM/YY format")
    parser.add_argument("--card-cvv", required=True)
    parser.add_argument("--first-name", required=True)
    parser.add_argument("--last-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--postal-code", required=True, dest="postcode")
    parser.add_argument("--country", required=True)
    
    # Email OTP için parametreler
    email_group = parser.add_argument_group('Email OTP Options')
    email_group.add_argument("--gmail-app-password", help="Gmail App Password (16 haneli kod) - IMAP için")
    email_group.add_argument("--gmail-api", action="store_true", help="Gmail API kullan (OAuth2)")
    email_group.add_argument("--gmail-credentials", default="credentials.json", help="Gmail API credentials dosyası")
    email_group.add_argument("--gmail-token", default="token.json", help="Gmail API token dosyası")
    
    # Diğer email servisleri için
    email_group.add_argument("--email-password", help="Email password (Gmail dışı servislerde)")
    email_group.add_argument("--email-imap-server", default="imap.gmail.com", help="IMAP server")
    email_group.add_argument("--email-imap-port", type=int, default=993, help="IMAP port")
    
    args = parser.parse_args()
    JSON_MODE = args.json
    
    _card_info = {
        'card_number': args.card_number,
        'expiry_date': args.card_expiry,
        'cvv': args.card_cvv,
        'first_name': args.first_name,
        'last_name': args.last_name
    }

    _customer_info = {
        'email': args.email,
        'phone': args.phone,
        'address': args.address,
        'city': args.city,
        'postcode': args.postcode,
        'country': args.country,
    }

    # Email config (opsiyonel)
    _email_config = None
    
    # Gmail API kullanımı
    if args.gmail_api and GMAIL_API_AVAILABLE:
        _email_config = {
            'use_gmail_api': True,
            'credentials_file': args.gmail_credentials,
            'token_file': args.gmail_token,
            'email': args.email
        }
        send_to_node("log", {"message": "Gmail API modu aktif - OAuth2 kullanılacak", "level": "info"})
    
    # Gmail App Password kullanımı
    elif args.gmail_app_password:
        _email_config = {
            'use_gmail_api': False,
            'email': args.email,
            'app_password': args.gmail_app_password,
            'imap_server': 'imap.gmail.com',
            'imap_port': 993
        }
        send_to_node("log", {"message": "Gmail App Password modu aktif - IMAP kullanılacak", "level": "info"})
    
    # Diğer email servisleri
    elif args.email_password:
        _email_config = {
            'use_gmail_api': False,
            'email': args.email,
            'app_password': args.email_password,  # Diğer servislerde normal password
            'imap_server': args.email_imap_server,
            'imap_port': args.email_imap_port
        }
        send_to_node("log", {"message": f"Email IMAP modu aktif - {args.email_imap_server} kullanılacak", "level": "info"})

    paybis_url = "https://paybis.com/"
    bot = None

    try:
        bot = PaybisBot(
            url=paybis_url,
            amount_eur=args.amount_eur,
            wallet_address=args.wallet_address,
            card_info=_card_info,
            customer_info=_customer_info,
            order_id=args.order_id,
            email_config=_email_config
        )
        
        success = bot.start()
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        send_to_node("log", {"message": f"[PaybisBot:{args.order_id}] Keyboard interrupt alındı.", "level": "warn"})
        if bot:
            bot.cleanup()
        sys.exit(1)
        
    except Exception as main_err:
        send_to_node("error", {"message": f"[PaybisBot:{args.order_id if 'args' in locals() and args.order_id else 'N/A'}] Kök hata: {str(main_err)} - {traceback.format_exc()}"})
        if bot:
            bot.cleanup()
        sys.exit(1)
        
    finally:
        if bot:
            bot.cleanup()
        send_to_node("log", {"message": f"[PaybisBot:{args.order_id if 'args' in locals() and args.order_id else 'N/A'}] Script sonlanıyor.", "level": "info"})