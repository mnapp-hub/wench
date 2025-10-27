import sqlite3
import os
import tarfile
from datetime import datetime
from threading import Lock

DB_PATH = '/app/data/totals.db'
BACKUP_DIR = '/app/backups'
db_lock = Lock()


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database - skip if file already exists and has data"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # If database file is larger than 5KB, it definitely has data - don't touch it
    if os.path.exists(DB_PATH):
        size = os.path.getsize(DB_PATH)
        if size > 5000:
            print(f"Database exists with {size} bytes - skipping init")
            return
    
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS amounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                month TEXT NOT NULL,
                amount REAL NOT NULL,
                kwh REAL,
                ocr_datetime TEXT,
                exif_datetime TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS image_hashes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_hash TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                exif_data TEXT,
                submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(image_hash, phone_number)
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_phone_month 
            ON amounts(phone_number, month)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_image_hash_phone 
            ON image_hashes(image_hash, phone_number)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_phone_amount_kwh 
            ON amounts(phone_number, amount, kwh)
        ''')
        
        conn.commit()
        conn.close()


def create_backup():
    """Create a tar.gz backup of the database"""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup_file = os.path.join(BACKUP_DIR, 'app-data-backup.tar.gz')
        
        with tarfile.open(backup_file, 'w:gz') as tar:
            tar.add(DB_PATH, arcname='totals.db')
        
        print(f"Backup created: {backup_file}")
        return backup_file
    except Exception as e:
        print(f"Error creating backup: {e}")
        raise


def add_amount(phone_number, month, amount, kwh=None, ocr_datetime=None, exif_datetime=None):
    """Add an amount, kWh, and both datetimes to the running total"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT INTO amounts (phone_number, month, amount, kwh, ocr_datetime, exif_datetime) VALUES (?, ?, ?, ?, ?, ?)',
            (phone_number, month, amount, kwh, ocr_datetime, exif_datetime)
        )
        
        conn.commit()
        conn.close()


def get_monthly_total(phone_number, month):
    """Get the running total for a specific user and month"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT SUM(amount) as total FROM amounts WHERE phone_number = ? AND month = ?',
            (phone_number, month)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        return result['total'] if result['total'] is not None else 0.0


def get_all_totals(month):
    """Get totals for all users for a specific month"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT phone_number, SUM(amount) as total FROM amounts WHERE month = ? GROUP BY phone_number',
            (month,)
        )
        
        results = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in results]


def get_user_history(phone_number):
    """Get all transactions for a user"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT * FROM amounts WHERE phone_number = ? ORDER BY timestamp DESC',
            (phone_number,)
        )
        
        results = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in results]


def check_image_hash(image_hash, phone_number):
    """Check if an image hash already exists for a specific user"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT id FROM image_hashes WHERE image_hash = ? AND phone_number = ?',
            (image_hash, phone_number)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        return result is not None


def check_duplicate_transaction(phone_number, amount, kwh):
    """Check if a user already has a transaction with the same amount and kWh"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT id FROM amounts WHERE phone_number = ? AND amount = ? AND kwh = ?',
            (phone_number, amount, kwh)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        return result is not None


def add_image_hash(image_hash, phone_number, exif_data):
    """Store an image hash with EXIF data for a specific user"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'INSERT INTO image_hashes (image_hash, phone_number, exif_data) VALUES (?, ?, ?)',
                (image_hash, phone_number, exif_data)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            print(f"Hash already exists for {phone_number}: {image_hash}")
        finally:
            conn.close()


def get_image_hash_history():
    """Get all stored image hashes and their details"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT image_hash, phone_number, exif_data, submitted_at FROM image_hashes ORDER BY submitted_at DESC'
        )
        
        results = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in results]
