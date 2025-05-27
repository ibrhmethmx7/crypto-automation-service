from flask import Flask, request, jsonify
import subprocess
import json
import threading

app = Flask(__name__)

@app.route('/process-payment', methods=['POST'])
def process_payment():
    data = request.json
    bot_type = data.get('bot_type', 'paybis')
    
    # Bot'u subprocess ile çalıştır
    cmd = [
        'python3', f'bots/{bot_type}_bot.py',
        '--json',
        '--order-id', data['order_id'],
        '--amount', str(data['amount']),
        '--wallet', data['wallet_address'],
        '--card-number', data['card_info']['card_number'],
        '--card-expiry', data['card_info']['expiry_date'],
        '--card-cvv', data['card_info']['cvv'],
        '--first-name', data['customer_info']['first_name'],
        '--last-name', data['customer_info']['last_name'],
        '--email', data['customer_info']['email'],
        '--phone', data['customer_info']['phone'],
        '--address', data['customer_info']['address'],
        '--city', data['customer_info']['city'],
        '--postal-code', data['customer_info']['postal_code'],
        '--country', data['customer_info']['country']
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode == 0:
            return jsonify({"success": True, "output": result.stdout})
        else:
            return jsonify({"success": False, "error": result.stderr})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)