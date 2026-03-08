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
        """Create cards and fallback post tables if not exists."""
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fallback_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL UNIQUE,
                asset_source TEXT NOT NULL DEFAULT 'emergency_fallback',
                fallback_version TEXT NOT NULL,
                fallback_trigger_stage TEXT NOT NULL,
                fallback_reason TEXT NOT NULL,
                publish_mode TEXT,
                title TEXT NOT NULL,
                scene_description TEXT NOT NULL,
                instagram_post_id TEXT,
                instagram_permalink TEXT,
                video_path_or_url TEXT,
                image_path_or_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        try:
            cursor.execute("SELECT publish_mode FROM fallback_posts LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE fallback_posts ADD COLUMN publish_mode TEXT")

        conn.commit()
        conn.close()

    def insert_card(self, card_data: dict):
        """Insert or update a card record for a run date."""
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
                ON CONFLICT(date) DO UPDATE SET
                    title=excluded.title,
                    scene_description=excluded.scene_description,
                    environment=excluded.environment,
                    creature=excluded.creature,
                    blend_option=excluded.blend_option,
                    energy_zone=excluded.energy_zone,
                    recovery_pct=excluded.recovery_pct,
                    sleep_score_pct=excluded.sleep_score_pct,
                    strain=excluded.strain,
                    sleep_hours=excluded.sleep_hours,
                    depth_level=excluded.depth_level,
                    dasha_maha=excluded.dasha_maha,
                    dasha_antar=excluded.dasha_antar,
                    dasha_pratyantar=excluded.dasha_pratyantar,
                    dasha_sookshma=excluded.dasha_sookshma,
                    dasha_prana=excluded.dasha_prana,
                    image_path=excluded.image_path,
                    video_path=excluded.video_path,
                    image_prompt_json=excluded.image_prompt_json,
                    instagram_post_id=excluded.instagram_post_id,
                    instagram_permalink=excluded.instagram_permalink
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
            print("✅ Successfully upserted card into database")
        except sqlite3.IntegrityError as e:
            print(f"❌ Failed to upsert card: {e}")
        finally:
            conn.close()

    def has_card_for_date(self, run_date: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            row = cursor.execute(
                "SELECT 1 FROM cards WHERE date = ? LIMIT 1",
                (run_date,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def insert_fallback_post(self, fallback_data: dict):
        """Insert a new emergency fallback post record."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO fallback_posts (
                    run_date, asset_source, fallback_version, fallback_trigger_stage,
                    fallback_reason, publish_mode, title, scene_description, instagram_post_id,
                    instagram_permalink, video_path_or_url, image_path_or_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_date) DO UPDATE SET
                    asset_source=excluded.asset_source,
                    fallback_version=excluded.fallback_version,
                    fallback_trigger_stage=excluded.fallback_trigger_stage,
                    fallback_reason=excluded.fallback_reason,
                    publish_mode=excluded.publish_mode,
                    title=excluded.title,
                    scene_description=excluded.scene_description,
                    instagram_post_id=excluded.instagram_post_id,
                    instagram_permalink=excluded.instagram_permalink,
                    video_path_or_url=excluded.video_path_or_url,
                    image_path_or_url=excluded.image_path_or_url
            """, (
                fallback_data.get('run_date', 'Unknown Date'),
                fallback_data.get('asset_source', 'emergency_fallback'),
                fallback_data.get('fallback_version', 'unknown'),
                fallback_data.get('fallback_trigger_stage', 'unknown'),
                fallback_data.get('fallback_reason', 'unknown'),
                fallback_data.get('publish_mode', ''),
                fallback_data.get('title', 'ERROR 404'),
                fallback_data.get('scene_description', 'Emergency fallback posted.'),
                fallback_data.get('instagram_post_id', ''),
                fallback_data.get('instagram_permalink', ''),
                fallback_data.get('video_path_or_url', ''),
                fallback_data.get('image_path_or_url', ''),
            ))
            conn.commit()
            print("✅ Successfully upserted fallback post into database")
        except sqlite3.IntegrityError as e:
            print(f"❌ Failed to upsert fallback post: {e}")
        finally:
            conn.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--insert', action='store_true', help='Insert a new record')
    parser.add_argument('--insert-fallback', action='store_true', help='Insert a new fallback record')
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

    if args.insert_fallback:
        if args.file:
            payload_path = Path(args.file)
        else:
            payload_path = get_output_root() / 'emergency_fallback_used.json'

        if payload_path.exists():
            with open(payload_path, 'r') as f:
                fallback_data = json.load(f)
            db.insert_fallback_post(fallback_data)
        else:
            print(f"❌ Could not find {payload_path}")

if __name__ == '__main__':
    main()
