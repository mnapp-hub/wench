import sqlite3
import os
from datetime import datetime
from threading import Lock

DB_PATH = '/app/data/totals.db'
db_lock = Lock()


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create table for storing amounts
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS amounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                month TEXT NOT NULL,
                amount REAL NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create table for image hashes (for duplicate detection)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS image_hashes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_hash TEXT UNIQUE NOT NULL,
                phone_number TEXT NOT NULL,
                exif_data TEXT,
                submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create indexes for faster queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_phone_month 
            ON amounts(phone_number, month)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_image_hash 
            ON image_hashes(image_hash)
        ''')
        
        conn.commit()
        conn.close()


def add_amount(phone_number, month, amount):
    """Add an amount to the running total"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT INTO amounts (phone_number, month, amount) VALUES (?, ?, ?)',
            (phone_number, month, amount)
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


def check_image_hash(image_hash):
    """Check if an image hash already exists in the database"""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT id FROM image_hashes WHERE image_hash = ?',
            (image_hash,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        return result is not None


def add_image_hash(image_hash, phone_number, exif_data):
    """Store an image hash with EXIF data for future duplicate detection"""
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
            # Hash already exists, this shouldn't happen since we check first
            print(f"Hash already exists: {image_hash}")
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
