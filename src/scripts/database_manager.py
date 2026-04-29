import sqlite3
import argparse
import sys
from pathlib import Path
import json

from creature_utils import split_creature_output
from environment_utils import split_environment_output
from utils import get_database_root, ensure_path, get_output_root

class CardDatabase:
    def __init__(self):
        self.db_dir = ensure_path(get_database_root())
        self.db_path = self.db_dir / 'cards.db'
        self.init_database()

    def init_database(self):
        """Create cards, fallback post, and environment history tables if not exists."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                scene_description TEXT NOT NULL,
                environment TEXT NOT NULL,
                environment_name TEXT,
                environment_reason TEXT,
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

        try:
            cursor.execute("SELECT environment_name FROM cards LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE cards ADD COLUMN environment_name TEXT")

        try:
            cursor.execute("SELECT environment_reason FROM cards LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE cards ADD COLUMN environment_reason TEXT")

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS environment_history (
                date TEXT PRIMARY KEY,
                energy_zone TEXT NOT NULL,
                environment_name TEXT NOT NULL,
                environment_text TEXT NOT NULL,
                selection_stage TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._backfill_environment_fields(cursor)
        self._backfill_environment_history(cursor)
        conn.commit()
        conn.close()

    def _backfill_environment_fields(self, cursor):
        rows = cursor.execute(
            """
            SELECT id, environment, environment_name, environment_reason
            FROM cards
            WHERE environment IS NOT NULL
              AND (environment_name IS NULL OR environment_reason IS NULL)
            """
        ).fetchall()

        updates = []
        for card_id, environment, environment_name, environment_reason in rows:
            parsed_name, parsed_reason = split_environment_output(environment or "")
            next_name = environment_name if environment_name is not None else (parsed_name or None)
            next_reason = environment_reason if environment_reason is not None else (parsed_reason or None)
            updates.append((next_name, next_reason, card_id))

        if updates:
            cursor.executemany(
                """
                UPDATE cards
                SET environment_name = ?, environment_reason = ?
                WHERE id = ?
                """,
                updates,
            )

    def _backfill_environment_history(self, cursor):
        rows = cursor.execute(
            """
            SELECT cards.date, cards.energy_zone, cards.environment_name, cards.environment
            FROM cards
            LEFT JOIN environment_history ON environment_history.date = cards.date
            WHERE environment_history.date IS NULL
              AND cards.date IS NOT NULL
              AND cards.energy_zone IS NOT NULL
              AND cards.environment IS NOT NULL
              AND COALESCE(cards.instagram_post_id, '') != ''
              AND cards.instagram_post_id NOT LIKE 'mock_%'
            ORDER BY cards.date ASC
            """
        ).fetchall()

        updates = []
        for run_date, energy_zone, environment_name, environment in rows:
            parsed_name, _parsed_reason = split_environment_output(environment or "")
            next_name = environment_name or parsed_name
            if not (run_date and energy_zone and next_name and environment):
                continue
            updates.append((run_date, energy_zone, next_name, environment, 'cards_backfill'))

        if updates:
            self._insert_missing_environment_history_many(cursor, updates)

    def _insert_missing_environment_history_many(self, cursor, rows):
        cursor.executemany(
            """
            INSERT INTO environment_history (
                date, energy_zone, environment_name, environment_text, selection_stage
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO NOTHING
            """,
            rows,
        )

    def _upsert_environment_history_many(self, cursor, rows):
        cursor.executemany(
            """
            INSERT INTO environment_history (
                date, energy_zone, environment_name, environment_text, selection_stage
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                energy_zone=excluded.energy_zone,
                environment_name=excluded.environment_name,
                environment_text=excluded.environment_text,
                selection_stage=excluded.selection_stage
            """,
            rows,
        )

    def insert_card(self, card_data: dict):
        """Insert or update a card record for a run date."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO cards (
                    date, title, scene_description, environment, environment_name,
                    environment_reason, creature, blend_option,
                    energy_zone, recovery_pct, sleep_score_pct, strain, sleep_hours,
                    depth_level, dasha_maha, dasha_antar, dasha_pratyantar,
                    dasha_sookshma, dasha_prana, image_path, video_path,
                    image_prompt_json, instagram_post_id, instagram_permalink
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    title=excluded.title,
                    scene_description=excluded.scene_description,
                    environment=excluded.environment,
                    environment_name=excluded.environment_name,
                    environment_reason=excluded.environment_reason,
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
                card_data.get('environment_name'),
                card_data.get('environment_reason'),
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
            environment_name = card_data.get('environment_name')
            environment_text = card_data.get('environment', 'Unknown')
            instagram_post_id = str(card_data.get('instagram_post_id', '') or '')
            if not environment_name:
                environment_name, _parsed_reason = split_environment_output(environment_text or "")
            if (
                card_data.get('date')
                and card_data.get('energy_zone')
                and environment_name
                and environment_text
                and instagram_post_id
                and not instagram_post_id.startswith('mock_')
            ):
                # Real posts promote the selected environment into archived history.
                self._upsert_environment_history_many(
                    cursor,
                    [
                        (
                            card_data.get('date'),
                            card_data.get('energy_zone'),
                            environment_name,
                            environment_text,
                            'cards_archive',
                        )
                    ],
                )
            conn.commit()
            print("✅ Successfully upserted card into database")
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"Failed to upsert card: {e}") from e
        finally:
            conn.close()

    def upsert_environment_history(
        self,
        *,
        run_date: str,
        energy_zone: str,
        environment_name: str,
        environment_text: str,
        selection_stage: str = 'environment_selected',
    ):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            self._upsert_environment_history_many(
                cursor,
                [
                    (
                        run_date,
                        energy_zone,
                        environment_name,
                        environment_text,
                        selection_stage,
                    )
                ],
            )
            conn.commit()
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"Failed to upsert environment history: {e}") from e
        finally:
            conn.close()

    def get_recent_environment_names(self, energy_zone: str, before_date: str, limit: int = 5) -> list[str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            # Repeat avoidance should see selected-but-not-yet-archived environments too.
            rows = cursor.execute(
                """
                SELECT environment_name, environment_text
                FROM environment_history
                WHERE energy_zone = ?
                  AND date < ?
                  AND selection_stage IN ('environment_selected', 'cards_archive', 'cards_backfill')
                ORDER BY date DESC
                LIMIT ?
                """,
                (energy_zone, before_date, limit),
            ).fetchall()

            names = []
            for environment_name, environment in rows:
                if environment_name:
                    names.append(environment_name)
                    continue
                parsed_name, _parsed_reason = split_environment_output(environment or "")
                if parsed_name:
                    names.append(parsed_name)
            return names
        finally:
            conn.close()

    def has_archived_environment_history_for_date(self, run_date: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            row = cursor.execute(
                """
                SELECT 1
                FROM environment_history
                WHERE date = ?
                  AND selection_stage IN ('cards_archive', 'cards_backfill')
                LIMIT 1
                """,
                (run_date,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def has_complete_archive_for_date(self, run_date: str) -> bool:
        return self.has_card_for_date(run_date) and self.has_archived_environment_history_for_date(run_date)

    def repair_selected_environment_history_from_output(self, run_dates: list[str]) -> int:
        if not run_dates:
            raise ValueError("repair_selected_environment_history_from_output requires explicit run_dates")

        output_root = get_output_root()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            rows = []
            for run_date in sorted(set(run_dates)):
                existing = cursor.execute(
                    """
                    SELECT 1
                    FROM environment_history
                    WHERE date = ?
                    LIMIT 1
                    """,
                    (run_date,),
                ).fetchone()
                if existing:
                    continue

                output_dir = output_root / run_date
                environment_path = output_dir / 'environment_selected.txt'
                daily_data_path = output_dir / 'daily_data.json'
                if not environment_path.exists() or not daily_data_path.exists():
                    continue

                try:
                    environment_text = environment_path.read_text(encoding='utf-8').strip()
                    daily_data = json.loads(daily_data_path.read_text(encoding='utf-8'))
                except Exception:
                    continue

                environment_name, _parsed_reason = split_environment_output(environment_text or "")
                energy_zone = daily_data.get('energy_zone')
                if not (run_date and energy_zone and environment_name and environment_text):
                    continue

                rows.append(
                    (
                        run_date,
                        energy_zone,
                        environment_name,
                        environment_text,
                        'environment_selected',
                    )
                )

            if rows:
                self._insert_missing_environment_history_many(cursor, rows)
                conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_recent_creature_names(self, before_date: str, limit: int = 10) -> list[str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            rows = cursor.execute(
                """
                SELECT creature
                FROM cards
                WHERE date < ?
                  AND COALESCE(instagram_post_id, '') != ''
                  AND COALESCE(instagram_post_id, '') NOT LIKE 'mock_%'
                ORDER BY date DESC
                LIMIT ?
                """,
                (before_date, limit),
            ).fetchall()

            names = []
            for (creature,) in rows:
                parsed_name, _parsed_reason = split_creature_output(creature or "")
                if parsed_name:
                    names.append(parsed_name)
            return names
        finally:
            conn.close()

    def get_recent_titles(self, before_date: str, limit: int = 10) -> list[str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            rows = cursor.execute(
                """
                SELECT title
                FROM cards
                WHERE date < ?
                  AND COALESCE(instagram_post_id, '') != ''
                  AND COALESCE(instagram_post_id, '') NOT LIKE 'mock_%'
                ORDER BY date DESC
                LIMIT ?
                """,
                (before_date, limit),
            ).fetchall()

            titles = []
            for (title,) in rows:
                if title:
                    titles.append(title)
            return titles
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
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"Failed to upsert fallback post: {e}") from e
        finally:
            conn.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--insert', action='store_true', help='Insert a new record')
    parser.add_argument('--insert-fallback', action='store_true', help='Insert a new fallback record')
    parser.add_argument('--repair-selected-history', action='store_true', help='Repair selected environment history from authoritative output dirs')
    parser.add_argument('--dates', nargs='*', help='Optional run dates for repair-selected-history')
    parser.add_argument('--file', help='Path to the JSON payload file')
    args = parser.parse_args()

    if args.insert:
        if args.file:
            payload_path = Path(args.file)
        else:
            payload_path = get_output_root() / 'last_archived_payload.json'
            
        if payload_path.exists():
            try:
                db = CardDatabase()
                with open(payload_path, 'r', encoding='utf-8') as f:
                    card_data = json.load(f)
                db.insert_card(card_data)
            except Exception as e:
                print(f"❌ Failed to archive card payload from {payload_path}: {e}")
                sys.exit(1)
        else:
            print(f"❌ Could not find {payload_path}")
            sys.exit(1)

    if args.insert_fallback:
        if args.file:
            payload_path = Path(args.file)
        else:
            payload_path = get_output_root() / 'emergency_fallback_used.json'

        if payload_path.exists():
            try:
                db = CardDatabase()
                with open(payload_path, 'r', encoding='utf-8') as f:
                    fallback_data = json.load(f)
                db.insert_fallback_post(fallback_data)
            except Exception as e:
                print(f"❌ Failed to archive fallback payload from {payload_path}: {e}")
                sys.exit(1)
        else:
            print(f"❌ Could not find {payload_path}")
            sys.exit(1)

    if args.repair_selected_history:
        if not args.dates:
            print("❌ --repair-selected-history requires one or more explicit --dates")
            sys.exit(1)
        try:
            repaired = CardDatabase().repair_selected_environment_history_from_output(args.dates)
            print(f"✅ Repaired {repaired} selected environment history rows")
        except Exception as e:
            print(f"❌ Failed to repair selected environment history: {e}")
            sys.exit(1)

if __name__ == '__main__':
    main()
