import sqlite3
import argparse
from pathlib import Path
import json

from utils import get_database_root, ensure_path, get_output_root

class CardDatabase:
    def __init__(self):
        self.db_dir = ensure_path(get_database_root())
        self.db_path = self.db_dir / 'cards.db'
        self.init_database()

    def init_database(self):
        """Create cards table if not exists"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                scene_description TEXT NOT NULL,
                environment TEXT NOT NULL,
                creature TEXT NOT NULL,
                blend_option TEXT NOT NULL,
                energy_zone TEXT NOT NULL,
                recovery_pct INTEGER NOT NULL,
                sleep_score_pct INTEGER NOT NULL,
                strain REAL NOT NULL,
                sleep_hours REAL NOT NULL,
                depth_level TEXT NOT NULL,
                dasha_maha TEXT NOT NULL,
                dasha_antar TEXT NOT NULL,
                dasha_pratyantar TEXT NOT NULL,
                dasha_sookshma TEXT NOT NULL,
                dasha_prana TEXT NOT NULL,
                image_path TEXT NOT NULL,
                video_path TEXT NOT NULL,
                image_prompt_json TEXT NOT NULL,
                instagram_post_id TEXT,
                instagram_permalink TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add instagram_permalink column if it doesn't exist (migration)
        try:
            cursor.execute("SELECT instagram_permalink FROM cards LIMIT 1")
        except sqlite3.OperationalError:
            # Column doesn't exist, add it
            cursor.execute("ALTER TABLE cards ADD COLUMN instagram_permalink TEXT")

        conn.commit()
        conn.close()

    def insert_card(self, card_data: dict):
        """Insert new card record"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO cards (
                    date, title, scene_description, environment, creature, blend_option,
                    energy_zone, recovery_pct, sleep_score_pct, strain, sleep_hours,
                    depth_level, dasha_maha, dasha_antar, dasha_pratyantar,
                    dasha_sookshma, dasha_prana, image_path, video_path,
                    image_prompt_json, instagram_post_id, instagram_permalink
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                card_data.get('date', 'Unknown Date'),
                card_data.get('title', 'Unknown Title'),
                card_data.get('scene_description', 'No description'),
                card_data.get('environment', 'Unknown'),
                card_data.get('creature', 'Unknown'),
                card_data.get('blend_option', 'Option A'),
                card_data.get('energy_zone', 'Unknown'),
                card_data.get('recovery_pct', 0),
                card_data.get('sleep_score_pct', 0),
                card_data.get('strain', 0.0),
                card_data.get('sleep_hours', 0.0),
                card_data.get('depth_level', 'Unknown'),
                card_data.get('dasha_maha', 'Unknown'),
                card_data.get('dasha_antar', 'Unknown'),
                card_data.get('dasha_pratyantar', 'Unknown'),
                card_data.get('dasha_sookshma', 'Unknown'),
                card_data.get('dasha_prana', 'Unknown'),
                card_data.get('image_path', ''),
                card_data.get('video_path', ''),
                card_data.get('image_prompt_json', '{}'),
                card_data.get('instagram_post_id', ''),
                card_data.get('instagram_permalink', '')
            ))
            conn.commit()
            print("✅ Successfully inserted card into database")
        except sqlite3.IntegrityError as e:
            print(f"❌ Failed to insert card - likely duplicate date: {e}")
        finally:
            conn.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--insert', action='store_true', help='Insert a new record')
    parser.add_argument('--file', help='Path to the JSON payload file')
    args = parser.parse_args()

    db = CardDatabase()

    if args.insert:
        if args.file:
            payload_path = Path(args.file)
        else:
            payload_path = get_output_root() / 'last_archived_payload.json'
            
        if payload_path.exists():
            with open(payload_path, 'r') as f:
                card_data = json.load(f)
            db.insert_card(card_data)
        else:
            print(f"❌ Could not find {payload_path}")

if __name__ == '__main__':
    main()
