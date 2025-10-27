import os
import re
import requests
import hashlib
from datetime import datetime
from dateutil.relativedelta import relativedelta
from flask import Flask, request, jsonify
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import pytesseract
import piexif
from PIL import Image
from PIL.ExifTags import TAGS
from io import BytesIO
from database import init_db, add_amount, get_monthly_total, get_all_totals, check_image_hash, add_image_hash, get_user_history, check_duplicate_transaction, create_backup

app = Flask(__name__)

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# Admin phone number
ADMIN_PHONE = os.getenv('ADMIN_PHONE', '+19402109661')

# Hardcoded user for admin transactions
ADMIN_ADD_USER = '+18179296991'

# Whitelist of allowed phone numbers (comma-separated in env variable)
WHITELIST = os.getenv('WHITELIST_NUMBERS', '').split(',')
WHITELIST = [phone.strip() for phone in WHITELIST if phone.strip()]

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Initialize database on startup
init_db()


def normalize_phone(phone):
    """Normalize phone number to E.164 format"""
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) >= 11 and not phone.startswith('+'):
        return f"+{digits}"
    return phone if phone.startswith('+') else f"+{digits}"


def is_whitelisted(phone_number):
    """Check if phone number is in whitelist"""
    if not WHITELIST:
        app.logger.warning("WHITELIST is empty - allowing all phone numbers!")
        return True
    return phone_number in WHITELIST


def is_admin(phone_number):
    """Check if phone number is admin"""
    return phone_number == ADMIN_PHONE


def log_unauthorized_attempt(phone_number, reason=""):
    """Log unauthorized access attempts"""
    app.logger.warning(f"Unauthorized SMS attempt from {phone_number}. Reason: {reason}")


def send_admin_notification(user_phone, amount, kwh, monthly_total, ocr_datetime, exif_datetime):
    """Send notification to admin about transaction"""
    try:
        exif_time_str = exif_datetime.strftime("%Y-%m-%d %H:%M:%S") if exif_datetime else "N/A"
        message = twilio_client.messages.create(
            body=f"💬 New transaction!\n\nUser: {user_phone}\nAmount: ${amount:.2f}\nkWh: {kwh}\nOCR Time: {ocr_datetime}\nEXIF Time: {exif_time_str}\nMonth total: ${monthly_total:.2f}",
            from_=TWILIO_PHONE_NUMBER,
            to=ADMIN_PHONE
        )
        print(f"Admin notification sent: {message.sid}")
    except Exception as e:
        print(f"Error sending admin notification: {e}")


def calculate_image_hash(image_bytes):
    """Calculate SHA256 hash of image"""
    return hashlib.sha256(image_bytes).hexdigest()


def extract_datetime_from_exif(img):
    """Extract datetime from image EXIF data"""
    try:
        exif_data = img._getexif()
        if exif_data:
            for tag_id in [36867, 306]:
                if tag_id in exif_data:
                    datetime_str = exif_data[tag_id]
                    return datetime.strptime(datetime_str, "%Y:%m:%d %H:%M:%S")
    except Exception as e:
        print(f"Error extracting EXIF datetime: {e}")
    return None


def extract_datetime_from_ocr(text):
    """Extract date and time from OCR text"""
    pattern1 = r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?'
    pattern2 = r'(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2})'
    pattern3 = r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})'
    
    for pattern in [pattern1, pattern2, pattern3]:
        match = re.search(pattern, text)
        if match:
            try:
                groups = match.groups()
                if len(groups) >= 5:
                    month, day, year, hour, minute = int(groups[0]), int(groups[1]), int(groups[2]), int(groups[3]), int(groups[4])
                    am_pm = groups[5] if len(groups) > 5 else None
                    if am_pm:
                        if am_pm.upper() == 'PM' and hour != 12:
                            hour += 12
                        elif am_pm.upper() == 'AM' and hour == 12:
                            hour = 0
                    return datetime(year, month, day, hour, minute)
            except Exception as e:
                print(f"Error parsing datetime: {e}")
    return None


def extract_exif_data(image):
    """Extract EXIF data from image"""
    exif_data = {}
    try:
        exif_dict = piexif.load(image.fp if hasattr(image, 'fp') else image.filename)
        for ifd_name in ("0th", "Exif", "GPS", "1st"):
            ifd = exif_dict.get(ifd_name)
            if ifd:
                for tag in ifd:
                    tag_name = piexif.TAGS[ifd_name][tag]["name"].decode()
                    try:
                        value = ifd[tag]
                        if isinstance(value, bytes):
                            value = value.decode('utf-8', errors='ignore')
                        exif_data[tag_name] = str(value)
                    except:
                        pass
    except Exception as e:
        print(f"Piexif extraction failed: {e}")
        try:
            exif_raw = image._getexif()
            if exif_raw:
                for tag_id, value in exif_raw.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    try:
                        if isinstance(value, bytes):
                            value = value.decode('utf-8', errors='ignore')
                        exif_data[tag_name] = str(value)
                    except:
                        pass
        except:
            pass
    return exif_data


def extract_money_and_kwh_from_image(image_url):
    """Download image from URL and extract money value, kWh, and datetimes using OCR"""
    try:
        response = requests.get(image_url)
        response.raise_for_status()
        image_bytes = response.content
        image_hash = calculate_image_hash(image_bytes)
        img = Image.open(BytesIO(image_bytes))
        exif_data = extract_exif_data(img)
        text = pytesseract.image_to_string(img)
        print(f"OCR extracted text: {text}")
        print(f"Image hash: {image_hash}")
        print(f"EXIF data: {exif_data}")
        
        money_pattern = r'\$[\d,]+\.?\d{0,2}'
        money_matches = re.findall(money_pattern, text)
        kwh_pattern = r'([\d,]+\.?\d*)\s*kWh'
        kwh_matches = re.findall(kwh_pattern, text, re.IGNORECASE)
        
        ocr_datetime = extract_datetime_from_ocr(text)
        if ocr_datetime is None:
            ocr_datetime = datetime.now()
        exif_datetime = extract_datetime_from_exif(img)
        
        print(f"OCR Datetime: {ocr_datetime}")
        print(f"EXIF Datetime: {exif_datetime}")
        
        amounts = None
        kwh_values = None
        
        if money_matches:
            amounts = [float(match.replace('$', '').replace(',', '')) for match in money_matches]
        if kwh_matches:
            kwh_values = [float(match.replace(',', '')) for match in kwh_matches]
        
        return amounts, kwh_values, text, image_hash, exif_data, ocr_datetime, exif_datetime
    except Exception as e:
        print(f"Error extracting money and kWh from image: {e}")
        return None, None, str(e), None, {}, None, None


def get_user_month_totals(phone_number):
    """Get all monthly totals for a user"""
    history = get_user_history(phone_number)
    monthly_totals = {}
    for transaction in history:
        month = transaction['month']
        amount = transaction['amount']
        if month not in monthly_totals:
            monthly_totals[month] = 0
        monthly_totals[month] += amount
    return monthly_totals


def handle_user_command(sender_phone, message_body):
    """Handle user commands"""
    command = message_body.strip().lower()
    
    if command == 'get total':
        current_month = datetime.now().strftime('%Y-%m')
        total = get_monthly_total(sender_phone, current_month)
        return f"💰 Current month ({current_month}): ${total:.2f}"
    elif command == 'get last total':
        last_month = (datetime.now() - relativedelta(months=1)).strftime('%Y-%m')
        total = get_monthly_total(sender_phone, last_month)
        return f"📅 Last month ({last_month}): ${total:.2f}"
    elif command == 'get all':
        monthly_totals = get_user_month_totals(sender_phone)
        if not monthly_totals:
            return "No transactions found."
        response_text = "📊 All monthly totals:\n\n"
        grand_total = 0
        for month in sorted(monthly_totals.keys(), reverse=True):
            total = monthly_totals[month]
            grand_total += total
            response_text += f"{month}: ${total:.2f}\n"
        response_text += f"\n🏆 Grand Total: ${grand_total:.2f}"
        return response_text
    else:
        return "❓ Unknown command. Available: 'get total', 'get last total', 'get all'"


def handle_admin_command(sender_phone, message_body):
    """Handle admin commands"""
    command = message_body.strip().lower()
    
    # Check for backup command
    if command == 'backup':
        try:
            backup_path = create_backup()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return f"✓ Backup created successfully!\nTimestamp: {timestamp}\nFile: app-data-backup.tar.gz"
        except Exception as e:
            return f"❌ Error creating backup: {str(e)}"
    
    # Check for manual entry command: "add kWh amount"
    if command.startswith('add '):
        parts = command.replace('add ', '').strip().split()
        if len(parts) >= 2:
            try:
                kwh = float(parts[0])
                amount = float(parts[1])
                
                # Check for duplicate transaction
                if check_duplicate_transaction(ADMIN_ADD_USER, amount, kwh):
                    return f"❌ Error: Duplicate transaction detected. {ADMIN_ADD_USER} already has ${amount:.2f} for {kwh} kWh"
                
                # Add to database
                current_month = datetime.now().strftime('%Y-%m')
                ocr_datetime_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                exif_datetime_str = "None"
                
                add_amount(ADMIN_ADD_USER, current_month, amount, kwh, ocr_datetime_str, exif_datetime_str)
                
                # Get updated total
                monthly_total = get_monthly_total(ADMIN_ADD_USER, current_month)
                
                # Send admin notification
                send_admin_notification(ADMIN_ADD_USER, amount, f"{kwh} kWh", monthly_total, ocr_datetime_str, None)
                
                return f"✓ Added manually: ${amount:.2f} ({kwh} kWh) to {ADMIN_ADD_USER}\nMonth total: ${monthly_total:.2f}"
            except ValueError:
                return "❌ Error: Invalid format. Use: add kWh amount (e.g., add 34.9 12.95)"
        else:
            return "❌ Error: Invalid format. Use: add kWh amount (e.g., add 34.9 12.95)"
    
    if command == 'status':
        current_month = datetime.now().strftime('%Y-%m')
        totals = get_all_totals(current_month)
        if not totals:
            return "No transactions this month."
        response_text = f"📊 Status for {current_month}:\n\n"
        for item in totals:
            phone = item['phone_number']
            if phone != ADMIN_PHONE:
                total = item['total']
                response_text += f"{phone}: ${total:.2f}\n"
        return response_text
    
    elif command.startswith('user '):
        user_input = command.replace('user ', '').strip()
        user_phone = normalize_phone(user_input)
        history = get_user_history(user_phone)
        if not history:
            return f"No transactions found for {user_phone}"
        response_text = f"📋 History for {user_phone}:\n\n"
        total = 0
        for transaction in history:
            amount = transaction['amount']
            kwh = transaction.get('kwh', 'N/A')
            ocr_datetime = transaction.get('ocr_datetime', 'N/A')
            exif_datetime = transaction.get('exif_datetime', 'N/A')
            month = transaction['month']
            total += amount
            response_text += f"${amount:.2f} ({kwh}) OCR:{ocr_datetime} EXIF:{exif_datetime}\n"
        response_text += f"\nTotal: ${total:.2f}"
        return response_text
    
    else:
        return "❓ Unknown admin command. Available: 'backup', 'add kWh amount', 'status', 'user [phone_number]'"


@app.route('/sms', methods=['POST'])
def handle_sms():
    """Handle incoming SMS/MMS from Twilio"""
    try:
        sender_phone = request.form.get('From')
        message_body = request.form.get('Body', '')
        num_media = int(request.form.get('NumMedia', 0))
        
        if not is_whitelisted(sender_phone):
            log_unauthorized_attempt(sender_phone, "Not in whitelist")
            response = MessagingResponse()
            response.message("Access denied. Your number is not authorized to use this service.")
            return str(response)
        
        if num_media > 0:
            media_url = request.form.get('MediaUrl0')
            amounts, kwh_values, ocr_text, image_hash, exif_data, ocr_datetime, exif_datetime = extract_money_and_kwh_from_image(media_url)
            
            if amounts is None or kwh_values is None:
                response = MessagingResponse()
                response.message("Sorry, I couldn't find both a dollar amount and kWh value in the image. Make sure the image contains both (e.g., $12.95 and 34.9 kWh)")
                return str(response)
            
            unique_amounts = set(amounts)
            if len(unique_amounts) > 1:
                response = MessagingResponse()
                response.message(f"❌ Error: Image has multiple different dollar amounts. Found: {', '.join([f'${a:.2f}' for a in sorted(unique_amounts)])}\n\nPlease send an image with only ONE dollar amount.")
                return str(response)
            
            unique_kwh = set(kwh_values)
            if len(unique_kwh) > 1:
                response = MessagingResponse()
                response.message(f"❌ Error: Image has multiple different kWh values. Found: {', '.join([f'{k} kWh' for k in sorted(unique_kwh)])}\n\nPlease send an image with only ONE kWh value.")
                return str(response)
            
            amount = amounts[0]
            kwh = kwh_values[0]
            kwh_str = f"{kwh} kWh"
            ocr_datetime_str = ocr_datetime.strftime("%Y-%m-%d %H:%M:%S")
            exif_datetime_str = exif_datetime.strftime("%Y-%m-%d %H:%M:%S") if exif_datetime else "None"
            
            transaction_phone = ADMIN_ADD_USER if is_admin(sender_phone) else sender_phone
            
            if check_image_hash(image_hash, transaction_phone):
                log_unauthorized_attempt(sender_phone, f"Duplicate image detected - hash: {image_hash}")
                response = MessagingResponse()
                response.message("❌ Error: You have already submitted this image. Please use a new screenshot with today's date visible.")
                return str(response)
            
            if check_duplicate_transaction(transaction_phone, amount, kwh):
                log_unauthorized_attempt(sender_phone, f"Duplicate transaction - ${amount:.2f} and {kwh_str}")
                response = MessagingResponse()
                response.message(f"❌ Error: Duplicate transaction detected. You already submitted ${amount:.2f} for {kwh_str}")
                return str(response)
            
            exif_str = str(exif_data)
            add_image_hash(image_hash, transaction_phone, exif_str)
            current_month = datetime.now().strftime('%Y-%m')
            add_amount(transaction_phone, current_month, amount, kwh, ocr_datetime_str, exif_datetime_str)
            monthly_total = get_monthly_total(transaction_phone, current_month)
            send_admin_notification(transaction_phone, amount, kwh_str, monthly_total, ocr_datetime_str, exif_datetime)
            
            response = MessagingResponse()
            if is_admin(sender_phone):
                response.message(f"✓ Added to {transaction_phone}: ${amount:.2f} ({kwh_str})\nTime: {ocr_datetime_str}\nTotal for {transaction_phone}: ${monthly_total:.2f}")
            else:
                response.message(f"✓ Added: ${amount:.2f} ({kwh_str})\nTime: {ocr_datetime_str}\nMonthly total: ${monthly_total:.2f}")
            
            return str(response)
        
        if message_body.strip():
            if is_admin(sender_phone):
                admin_response = handle_admin_command(sender_phone, message_body)
                response = MessagingResponse()
                response.message(admin_response)
                return str(response)
            
            user_response = handle_user_command(sender_phone, message_body)
            response = MessagingResponse()
            response.message(user_response)
            return str(response)
        
        response = MessagingResponse()
        response.message("Please send an image with a dollar amount and kWh value.")
        return str(response)
    
    except Exception as e:
        print(f"Error handling SMS: {e}")
        response = MessagingResponse()
        response.message(f"Error: {str(e)}")
        return str(response)


@app.route('/status', methods=['GET'])
def get_status():
    """Get current monthly totals for all users"""
    try:
        current_month = datetime.now().strftime('%Y-%m')
        totals = get_all_totals(current_month)
        return jsonify({'month': current_month, 'totals': totals})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
