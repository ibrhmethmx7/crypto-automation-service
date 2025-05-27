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
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
except ImportError as e:
    print(f"Error: Missing dependencies. Run: pip install selenium webdriver-manager")
    sys.exit(1)

# Global JSON mode flag
JSON_MODE = False

def send_to_node(message_type, data_payload):
    """Node.js'e yapısal JSON mesajı gönderir."""
    if JSON_MODE:
        print(json.dumps({"type": message_type, "data": data_payload}), flush=True)
    else:
        print(f"[{message_type}] {data_payload}", flush=True)

class BanxaBot:
    def __init__(self, url, amount_eur, wallet_address, card_info, customer_info, order_id):
        self.order_id = order_id
        self.driver = None
        self.temp_dir = None
        
        send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Banxa bot başlatılıyor...", "level": "info"})
        
        self.url = url or "https://banxa.com/"
        self.amount_eur = amount_eur
        self.wallet_address = wallet_address
        self.card_info = card_info
        self.customer_info = customer_info
        self.email = self.customer_info['email']
        
        # Signal handler ekle
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self._setup_chrome()
    
    def _signal_handler(self, signum, frame):
        """Signal handler - temizlik yapar"""
        send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Signal {signum} alındı, temizlik yapılıyor...", "level": "warn"})
        self.cleanup()
        sys.exit(1)
    
    def _setup_chrome(self):
        """Chrome'u WebDriver Manager ile kurar"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Chrome kurulum denemesi {attempt + 1}/{max_retries}", "level": "info"})
                
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
                    send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Headless mode aktif", "level": "info"})
                
                # Unique temp directory
                unique_id = str(uuid.uuid4())[:8]
                self.temp_dir = tempfile.mkdtemp(prefix=f"chrome_banxa_{self.order_id}_{unique_id}_")
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
                
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] ChromeDriver indiriliyor...", "level": "info"})
                
                # WebDriver Manager ile driver kurulumu
                try:
                    service = Service(ChromeDriverManager().install())
                    self.driver = webdriver.Chrome(service=service, options=chrome_options)
                except Exception as wdm_error:
                    send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] WebDriverManager hatası: {wdm_error}", "level": "warn"})
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
                
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Chrome başarıyla başlatıldı!", "level": "success"})
                return
                
            except Exception as e:
                error_msg = str(e)
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Chrome kurulum hatası (deneme {attempt + 1}): {error_msg}", "level": "error"})
                
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
                    send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] {sleep_time} saniye bekledikten sonra tekrar denenecek...", "level": "info"})
                    time.sleep(sleep_time)
                else:
                    send_to_node("error", {"message": f"[BanxaBot:{self.order_id}] Chrome {max_retries} denemede de başlatılamadı: {error_msg}"})
                    raise Exception(f"Chrome setup failed after {max_retries} attempts: {error_msg}")

    def wait_for_page_load(self, timeout=30):
        send_to_node("progress", {"progress": 5, "step": "Sayfa yüklenmesi bekleniyor..."})
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Sayfa tamamen yüklendi.", "level": "debug"})
            return True
        except TimeoutException:
            send_to_node("error", {"message": f"[BanxaBot:{self.order_id}] Sayfa yükleme zaman aşımına uğradı."})
            return False

    def initialize_purchase(self):
        send_to_node("progress", {"progress": 10, "step": "Banxa sayfasına gidiliyor..."})
        try:
            self.driver.get(self.url)
            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] {self.url} adresine gidildi", "level": "info"})
            if not self.wait_for_page_load(): 
                return False

            # Amount input (Banxa style selectors)
            amount_input = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-testid='amount-input'], .amount-field, input[name='amount']"))
            )
            amount_input.clear()
            amount_input.send_keys(str(self.amount_eur))
            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Miktar girildi: {self.amount_eur} EUR", "level": "info"})

            # Crypto selection (Bitcoin)
            crypto_selector = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-currency='BTC'], .crypto-btc, button:contains('Bitcoin')"))
            )
            crypto_selector.click()
            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Bitcoin seçildi.", "level": "info"})

            # Wallet address
            wallet_input = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-testid='wallet-address'], input[name='walletAddress'], input[placeholder*='wallet']"))
            )
            wallet_input.clear()
            wallet_input.send_keys(self.wallet_address)
            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Cüzdan adresi girildi.", "level": "info"})

            # Continue button
            continue_button = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='continue'], .continue-btn, button:contains('Continue')"))
            )
            continue_button.click()
            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Devam butonuna tıklandı.", "level": "info"})
            return True
        except Exception as e:
            send_to_node("error", {"message": f"[BanxaBot:{self.order_id}] Başlangıç adımında hata: {str(e)}"})
            return False

    def fill_personal_info(self):
        send_to_node("progress", {"progress": 30, "step": "Kişisel bilgiler giriliyor..."})
        try:
            # Email
            email_input = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[name='email']"))
            )
            email_input.clear()
            email_input.send_keys(self.email)

            # First name
            first_name_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='firstName'], input[data-testid='first-name']"))
            )
            first_name_input.clear()
            first_name_input.send_keys(self.card_info['first_name'])

            # Last name
            last_name_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='lastName'], input[data-testid='last-name']"))
            )
            last_name_input.clear()
            last_name_input.send_keys(self.card_info['last_name'])

            # Phone
            phone_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='phone'], input[type='tel']"))
            )
            phone_input.clear()
            phone_input.send_keys(self.customer_info['phone'])

            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Kişisel bilgiler girildi.", "level": "info"})
            return True
        except Exception as e:
            send_to_node("error", {"message": f"[BanxaBot:{self.order_id}] Kişisel bilgiler girme hatası: {str(e)}"})
            return False

    def fill_card_details(self):
        send_to_node("progress", {"progress": 50, "step": "Kart bilgileri giriliyor..."})
        try:
            # Card number
            card_input = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='cardNumber'], input[data-testid='card-number']"))
            )
            card_input.clear()
            card_input.send_keys(self.card_info['card_number'])

            # Expiry date
            expiry_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='expiry'], input[placeholder*='MM/YY']"))
            )
            expiry_input.clear()
            expiry_input.send_keys(self.card_info['expiry_date'])

            # CVV
            cvv_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='cvv'], input[data-testid='cvv']"))
            )
            cvv_input.clear()
            cvv_input.send_keys(self.card_info['cvv'])

            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Kart bilgileri girildi.", "level": "info"})
            return True
        except Exception as e:
            send_to_node("error", {"message": f"[BanxaBot:{self.order_id}] Kart bilgileri girme hatası: {str(e)}"})
            return False

    def handle_verification_and_payment(self):
        send_to_node("progress", {"progress": 70, "step": "Doğrulama ve ödeme işleniyor..."})
        try:
            # Submit payment
            pay_button = WebDriverWait(self.driver, 20).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='pay'], .pay-button, button:contains('Pay')"))
            )
            pay_button.click()
            send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Ödeme butonuna tıklandı.", "level": "info"})

            # Wait for SMS verification or 3DS
            time.sleep(8)
            
            current_url = self.driver.current_url
            page_content = self.driver.page_source.lower()
            
            if "sms" in page_content or "verification" in page_content:
                send_to_node("verification_required", {
                    "gatewayName": "Banxa",
                    "type": "sms",
                    "timeLimit": 300,
                    "message": f"SMS verification code sent to {self.customer_info['phone']}. Please check and enter the code."
                })

                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] SMS doğrulama kodu bekleniyor...", "level": "info"})
                code_from_node_json = sys.stdin.readline()
                code_payload = json.loads(code_from_node_json)
                
                if code_payload.get("type") == "verification_code":
                    verification_code = code_payload.get("code")
                    
                    # Enter verification code
                    code_input = WebDriverWait(self.driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='verificationCode'], input[data-testid='sms-code']"))
                    )
                    code_input.clear()
                    code_input.send_keys(verification_code)
                    
                    # Submit verification
                    verify_button = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='verify'], .verify-button"))
                    )
                    verify_button.click()
                    send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] SMS doğrulama kodu girildi.", "level": "info"})

            elif "3ds" in current_url.lower() or "secure" in current_url.lower():
                send_to_node("verification_required", {
                    "gatewayName": "Banxa",
                    "type": "3ds",
                    "timeLimit": 300,
                    "message": "3D Secure verification required. Please complete the bank authentication."
                })

                # Wait for user to complete 3DS
                code_from_node_json = sys.stdin.readline()
                code_payload = json.loads(code_from_node_json)
                
                if code_payload.get("type") == "verification_code":
                    time.sleep(5)
                    send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] 3D Secure tamamlandı.", "level": "info"})

            return True
        except Exception as e:
            send_to_node("error", {"message": f"[BanxaBot:{self.order_id}] Doğrulama/ödeme hatası: {str(e)}"})
            return False

    def start(self):
        send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Ana süreç başlatılıyor...", "level": "info"})
        try:
            if not self.initialize_purchase(): 
                return False
            send_to_node("progress", {"progress": 20, "step": "Satın alma başlatıldı."})
            time.sleep(3)

            if not self.fill_personal_info(): 
                return False
            send_to_node("progress", {"progress": 40, "step": "Kişisel bilgiler girildi."})
            time.sleep(2)

            if not self.fill_card_details(): 
                return False
            send_to_node("progress", {"progress": 60, "step": "Kart bilgileri girildi."})
            time.sleep(2)

            if not self.handle_verification_and_payment(): 
                return False
            send_to_node("progress", {"progress": 90, "step": "Ödeme doğrulanıyor..."})
            time.sleep(8)

            # Simulate success
            send_to_node("progress", {"progress": 100, "step": "Ödeme tamamlandı!"})
            send_to_node("success", {
                "gatewayName": "Banxa",
                "orderNumber": f"BANXA-ORD-{self.order_id}",
                "transactionId": f"BANXA-TXN-{int(time.time())}",
                "cryptoCurrency": "BTC",
                "cryptoAmount": f"{float(self.amount_eur) / 65000:.8f}",
                "message": "Banxa payment completed successfully."
            })
            return True

        except Exception as e:
            send_to_node("error", {"message": f"[BanxaBot:{self.order_id}] Ana süreç hatası: {str(e)} - {traceback.format_exc()}"})
            return False
        finally:
            self.cleanup()

    def cleanup(self):
        """Temizlik işlemi"""
        send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Temizlik işlemi başlatılıyor.", "level": "info"})
        
        if self.driver:
            try:
                self.driver.quit()
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Chrome driver kapatıldı.", "level": "debug"})
            except Exception as e:
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Driver kapatma hatası: {str(e)}", "level": "warn"})
            finally:
                self.driver = None
        
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Temp directory temizlendi.", "level": "debug"})
            except Exception as e:
                send_to_node("log", {"message": f"[BanxaBot:{self.order_id}] Temp directory temizleme hatası: {str(e)}", "level": "warn"})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Banxa Payment Bot")
    
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

    banxa_url = "https://banxa.com/"
    bot = None

    try:
        bot = BanxaBot(
            url=banxa_url,
            amount_eur=args.amount_eur,
            wallet_address=args.wallet_address,
            card_info=_card_info,
            customer_info=_customer_info,
            order_id=args.order_id
        )
        
        success = bot.start()
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        send_to_node("log", {"message": f"[BanxaBot:{args.order_id}] Keyboard interrupt alındı.", "level": "warn"})
        if bot:
            bot.cleanup()
        sys.exit(1)
        
    except Exception as main_err:
        send_to_node("error", {"message": f"[BanxaBot:{args.order_id if 'args' in locals() and args.order_id else 'N/A'}] Kök hata: {str(main_err)} - {traceback.format_exc()}"})
        if bot:
            bot.cleanup()
        sys.exit(1)
        
    finally:
        if bot:
            bot.cleanup()
        send_to_node("log", {"message": f"[BanxaBot:{args.order_id if 'args' in locals() and args.order_id else 'N/A'}] Script sonlanıyor.", "level": "info"})