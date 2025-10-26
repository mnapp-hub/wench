# SMS OCR Money Tracker

A Docker-based app that receives SMS/MMS images from Twilio, extracts dollar amounts using OCR, performs math operations, and sends results back via text.

## Features

- Receives MMS messages with images from Twilio
- Extracts dollar amounts from images using Tesseract OCR
- Maintains a running monthly total per user
- Sends SMS confirmation with updated total
- SQLite database for persistent storage
- REST API endpoint to check monthly totals
- Health check endpoint for monitoring

## Prerequisites

- Docker & Docker Compose
- Twilio account with a phone number configured
- ngrok or similar for exposing your local server to the internet (or deploy to a cloud server)

## Setup

### 1. Clone/Download the app

```bash
cd sms-ocr-app
```

### 2. Create .env file

Copy `.env.example` to `.env` and fill in your Twilio credentials:

```bash
cp .env.example .env
```

Edit `.env`:
```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+1234567890

# Add your whitelisted phone numbers (comma-separated, E.164 format with +)
WHITELIST_NUMBERS=+1234567890,+0987654321,+1111111111
```

**Important**: Phone numbers must be in E.164 format (international format with + prefix). Without a whitelist, the app will allow all numbers.

### 3. Build and run with Docker Compose

```bash
docker-compose up -d
```

This will:
- Build the Docker image
- Start the Flask app on port 5000
- Create a `data/` directory for the SQLite database

### 4. Configure Twilio Webhook

In your Twilio console:
1. Go to your phone number settings
2. Under "Messaging", set the "A Message Comes In" webhook to your server's URL
3. For local development with ngrok: `https://your-ngrok-url.ngrok.io/sms`
4. For production: `https://your-domain.com/sms`
5. Make sure it's set to POST

### 5. Test it

Send an MMS to your Twilio number with an image containing a dollar amount like "$12.95"

You should get a response confirming the amount and showing the running monthly total.

## API Endpoints

### POST /sms
Webhook endpoint for incoming Twilio messages (automatic)

### GET /status
Get current month's totals for all users
```bash
curl http://localhost:5000/status
```

Response:
```json
{
  "month": "2025-10",
  "totals": [
    {
      "phone_number": "+1234567890",
      "total": 150.25
    }
  ]
}
```

### GET /health
Health check endpoint
```bash
curl http://localhost:5000/health
```

## How It Works

1. User sends MMS with image to your Twilio number
2. Twilio POSTs to `/sms` endpoint with image URL
3. App downloads image from URL
4. **Image hash (SHA256) is calculated** - used to detect reused images
5. **EXIF metadata extracted** - device info, timestamps, GPS data if available
6. **Duplicate check** - if same image hash exists, request is rejected
7. Tesseract OCR extracts all text
8. Regex pattern finds dollar amounts (e.g., $12.95)
9. Amount added to database under current month
10. Image hash stored permanently for future duplicate detection
11. Response sent back with confirmation and updated total

## Database

SQLite database stored in `data/totals.db` with two main tables:

**amounts table** - stores all money entries:
```sql
amounts (
  id INTEGER,
  phone_number TEXT,
  month TEXT (YYYY-MM),
  amount REAL,
  timestamp DATETIME
)
```

**image_hashes table** - stores image fingerprints for duplicate detection:
```sql
image_hashes (
  id INTEGER,
  image_hash TEXT (SHA256 hash - unique),
  phone_number TEXT,
  exif_data TEXT (JSON-like string of metadata),
  submitted_at DATETIME
)
```

### Duplicate Detection

- Every image is hashed with SHA256 algorithm
- Hash is permanently stored and checked on each submission
- If same image is submitted again, request is rejected immediately
- EXIF metadata (device info, timestamps, etc.) is stored for audit purposes
- This prevents users from reusing the same screenshot with different amounts

## Troubleshooting

### App won't start
```bash
docker-compose logs -f
```

### OCR not extracting text
- Check image quality and resolution
- Ensure dollar amount is clearly visible
- Try different image formats

### Twilio webhook not triggering
- Verify webhook URL is correct and public
- Check Twilio logs for errors
- Ensure POST method is selected

### Duplicate image rejection
- Users trying to reuse the same screenshot get error message
- Image hashes are kept forever for security
- Check app logs to see which images were rejected

### EXIF extraction not working
- Some iPhone screenshots may have minimal EXIF data
- App still prevents duplicates via hash even if EXIF is empty
- EXIF data is logged but not required for duplicate detection

## Docker Commands

```bash
# Start
docker-compose up -d

# Stop
docker-compose down

# View logs
docker-compose logs -f

# Rebuild
docker-compose up -d --build

# Access container shell
docker-compose exec sms-ocr-app bash
```

## Customization

### Change OCR regex pattern
Edit the `money_pattern` in `app.py` to match your specific format

### Handle multiple dollar amounts
Modify the `extract_money_from_image()` function to sum all amounts instead of taking the first

### Connect to external database
Replace the SQLite logic in `database.py` with your preferred database

## Environment Variables

- `TWILIO_ACCOUNT_SID` - Your Twilio account SID
- `TWILIO_AUTH_TOKEN` - Your Twilio auth token
- `TWILIO_PHONE_NUMBER` - Your Twilio phone number
- `WHITELIST_NUMBERS` - Comma-separated list of authorized phone numbers (E.164 format)

## Security

### Phone Number Whitelist

The app includes phone number whitelisting to restrict access:

- **Whitelist enabled**: Only phone numbers in `WHITELIST_NUMBERS` can use the service
- **Whitelist empty**: All phone numbers are allowed (for development only)
- **Unauthorized attempts**: Logged with the sender's phone number

**Example .env configuration**:
```
WHITELIST_NUMBERS=+12345678900,+19876543210,+14155552671
```

**Format**: Phone numbers must be in E.164 international format with the `+` prefix (e.g., `+1-234-567-8900` becomes `+12345678900`)

**How to get E.164 format**:
- US numbers: `+1` + 10 digits (e.g., `+12125552368`)
- International: Country code + number (e.g., Canada `+14165552368`)

### Logging

Unauthorized access attempts are logged with:
- Sender's phone number
- Reason for denial
- Timestamp (in app logs)

Check logs with:
```bash
docker-compose logs -f
```

### Other Security Considerations

- Store `.env` file securely and never commit to version control
- Use HTTPS in production (add reverse proxy like nginx)
- Regularly rotate Twilio credentials
- Monitor application logs for suspicious activity
- Consider rate limiting for future versions
