import os
import re
import requests
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import pytesseract
import piexif
from PIL import Image
from PIL.ExifTags import TAGS
from io import BytesIO
from database import init_db, add_amount, get_monthly_total, get_all_totals, check_image_hash, add_image_hash

app = Flask(__name__)

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# Whitelist of allowed phone numbers (comma-separated in env variable)
WHITELIST = os.getenv('WHITELIST_NUMBERS', '').split(',')
WHITELIST = [phone.strip() for phone in WHITELIST if phone.strip()]

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Initialize database on startup
init_db()


def is_whitelisted(phone_number):
    """Check if phone number is in whitelist"""
    if not WHITELIST:
        # If whitelist is empty, allow all (for development)
        app.logger.warning("WHITELIST is empty - allowing all phone numbers!")
        return True
    
    return phone_number in WHITELIST


def log_unauthorized_attempt(phone_number, reason=""):
    """Log unauthorized access attempts"""
    app.logger.warning(f"Unauthorized SMS attempt from {phone_number}. Reason: {reason}")


def calculate_image_hash(image_bytes):
    """Calculate SHA256 hash of image"""
    return hashlib.sha256(image_bytes).hexdigest()


def extract_exif_data(image):
    """Extract EXIF data from image"""
    exif_data = {}
    try:
        # Try piexif for more detailed EXIF extraction
        exif_dict = piexif.load(image.fp if hasattr(image, 'fp') else image.filename)
        
        for ifd_name in ("0th", "Exif", "GPS", "1st"):
            ifd = exif_dict.get(ifd_name)
            if ifd:
                for tag in ifd:
                    tag_name = piexif.TAGS[ifd_name][tag]["name"].decode()
                    try:
                        value = ifd[tag]
                        # Convert bytes to string if needed
                        if isinstance(value, bytes):
                            value = value.decode('utf-8', errors='ignore')
                        exif_data[tag_name] = str(value)
                    except:
                        pass
    except Exception as e:
        print(f"Piexif extraction failed: {e}")
        # Fallback to PIL EXIF extraction
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


def extract_money_from_image(image_url):
    """Download image from URL and extract money value using OCR"""
    try:
        # Download the image
        response = requests.get(image_url)
        response.raise_for_status()
        
        image_bytes = response.content
        
        # Calculate image hash
        image_hash = calculate_image_hash(image_bytes)
        
        # Open image
        img = Image.open(BytesIO(image_bytes))
        
        # Extract EXIF data
        exif_data = extract_exif_data(img)
        
        # Run OCR
        text = pytesseract.image_to_string(img)
        print(f"OCR extracted text: {text}")
        print(f"Image hash: {image_hash}")
        print(f"EXIF data: {exif_data}")
        
        # Find dollar amounts in the text (e.g., $12.95, $100, etc.)
        # Pattern matches: $12.95, $1,234.56, etc.
        money_pattern = r'\$[\d,]+\.?\d{0,2}'
        matches = re.findall(money_pattern, text)
        
        if matches:
            # Convert to float (remove $ and commas)
            amounts = [float(match.replace('$', '').replace(',', '')) for match in matches]
            # Return the first match (you can adjust this logic if needed)
            return amounts[0], text, image_hash, exif_data
        
        return None, text, image_hash, exif_data
    
    except Exception as e:
        print(f"Error extracting money from image: {e}")
        return None, str(e), None, {}


@app.route('/sms', methods=['POST'])
def handle_sms():
    """Handle incoming SMS/MMS from Twilio"""
    try:
        # Get sender's phone number
        sender_phone = request.form.get('From')
        
        # Check if phone number is whitelisted
        if not is_whitelisted(sender_phone):
            log_unauthorized_attempt(sender_phone, "Not in whitelist")
            response = MessagingResponse()
            response.message(
                "Access denied. Your number is not authorized to use this service."
            )
            return str(response)
        
        # Check if there are media attachments
        num_media = int(request.form.get('NumMedia', 0))
        
        if num_media > 0:
            # Get the first image URL
            media_url = request.form.get('MediaUrl0')
            
            # Extract money value from image
            amount, ocr_text, image_hash, exif_data = extract_money_from_image(media_url)
            
            if amount is not None:
                # Check if this image has been used before
                if check_image_hash(image_hash):
                    log_unauthorized_attempt(sender_phone, f"Duplicate image detected - hash: {image_hash}")
                    response = MessagingResponse()
                    response.message(
                        "❌ Error: This image has already been submitted. "
                        "Please use a new screenshot with today's date visible."
                    )
                    return str(response)
                
                # Add image hash to database
                exif_str = str(exif_data)
                add_image_hash(image_hash, sender_phone, exif_str)
                
                # Add to database
                current_month = datetime.now().strftime('%Y-%m')
                add_amount(sender_phone, current_month, amount)
                
                # Get updated total for the month
                monthly_total = get_monthly_total(sender_phone, current_month)
                
                # Create response
                response = MessagingResponse()
                response.message(
                    f"✓ Added: ${amount:.2f}\n"
                    f"Monthly total: ${monthly_total:.2f}"
                )
                
                return str(response)
            else:
                # No money found in image
                response = MessagingResponse()
                response.message(
                    "Sorry, I couldn't find a dollar amount in the image. "
                    "Make sure the image contains a clear dollar value like $12.95"
                )
                return str(response)
        else:
            # No image attached
            response = MessagingResponse()
            response.message("Please send an image with a dollar amount.")
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
        return jsonify({
            'month': current_month,
            'totals': totals
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
