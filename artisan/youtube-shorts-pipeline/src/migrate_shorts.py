#!/usr/bin/env python3
"""
One-time migration script to restructure shorts directory from legacy flat layout
to niche-organized layout.

Legacy: data/shorts/<video_title>/clip_N.mp4
New:    data/shorts/<niche>/<video_title>/clip_N.mp4

This script:
1. Queries the database for all generated_shorts with local_path
2. Detects legacy paths (not already under a niche folder)
3. Moves physical files using shutil.move
4. Updates local_path in SQLite in a single transaction
5. Removes empty legacy video title folders
6. Normalizes all paths to POSIX format for cross-platform consistency

Usage:
    python -m src.migrate_shorts [--dry-run] [--force]
"""

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple

# Ensure imports work when run as script or module
try:
    from .config import config
    from .utils import setup_logger
except ImportError:
    # Fallback for direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import config
    from utils import setup_logger


logger = setup_logger(__name__, log_file=Path(config.logs_dir) / 'migrate_shorts.log')


def is_legacy_path(shorts_dir: Path, local_path: str) -> bool:
    """
    Check if a local_path uses the legacy structure (no niche folder).
    
    Legacy: data/shorts/<video_title>/clip_N.mp4
    New:    data/shorts/<niche>/<video_title>/clip_N.mp4
    """
    path = Path(local_path)
    try:
        # Get the relative path from shorts_dir
        rel = path.relative_to(shorts_dir)
        # Legacy has 2 parts: video_title / clip_N.mp4
        # New has 3 parts: niche / video_title / clip_N.mp4
        return len(rel.parts) == 2
    except ValueError:
        # Path is not under shorts_dir at all
        return False


def get_niche_for_video(db_path: Path, source_video_id: str) -> str:
    """Get the niche for a source video from the database."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT niche FROM processed_videos WHERE youtube_video_id = ?",
                (source_video_id,)
            ).fetchone()
            if row and row['niche']:
                return row['niche']
    except Exception as exc:
        logger.warning("Could not get niche for %s: %s", source_video_id, exc)
    return ''


def migrate_shorts(dry_run: bool = False, force: bool = False) -> Tuple[int, int, int]:
    """
    Migrate legacy shorts structure to niche-organized structure.
    
    Returns:
        (moved_count, updated_count, errors_count)
    """
    shorts_dir = Path(config.shorts_dir)
    db_path = Path(config.db_path)
    
    if not shorts_dir.exists():
        logger.info("Shorts directory does not exist: %s", shorts_dir)
        return (0, 0, 0)
    
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        return (0, 0, 1)
    
    moved = 0
    updated = 0
    errors = 0
    
    # Query all shorts with local_path
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT gs.id, gs.source_video_id, gs.segment_index, gs.local_path,
                       COALESCE(pv.niche, '') AS niche
                FROM generated_shorts gs
                LEFT JOIN processed_videos pv
                    ON pv.youtube_video_id = gs.source_video_id
                WHERE gs.local_path IS NOT NULL AND gs.local_path != ''
            """).fetchall()
    except Exception as exc:
        logger.error("Failed to query generated_shorts: %s", exc)
        return (0, 0, 1)
    
    if not rows:
        logger.info("No shorts records found to migrate")
        return (0, 0, 0)
    
    logger.info("Found %d shorts records to check", len(rows))
    
    # Collect migrations needed
    migrations = []
    for row in rows:
        local_path = row['local_path']
        source_video_id = row['source_video_id']
        segment_index = row['segment_index']
        niche = row['niche'] or get_niche_for_video(db_path, source_video_id)
        
        if not niche:
            logger.warning("No niche found for %s#%s, skipping", source_video_id, segment_index)
            errors += 1
            continue
        
        if is_legacy_path(shorts_dir, local_path):
            # Legacy path detected
            path = Path(local_path)
            video_title = path.parent.name
            clip_name = path.name
            
            new_path = shorts_dir / niche / video_title / clip_name
            migrations.append({
                'id': row['id'],
                'old_path': path,
                'new_path': new_path,
                'niche': niche,
            })
        else:
            # Already in new structure or unknown format
            pass
    
    if not migrations:
        logger.info("No legacy paths found - already migrated")
        return (0, 0, 0)
    
    logger.info("Found %d legacy shorts to migrate", len(migrations))
    
    if dry_run:
        logger.info("DRY RUN - would perform the following moves:")
        for m in migrations:
            logger.info("  %s -> %s", m['old_path'], m['new_path'])
        return (len(migrations), 0, 0)
    
    # Perform migrations in a single transaction
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            
            for m in migrations:
                old_path = m['old_path']
                new_path = m['new_path']
                
                # Ensure destination directory exists
                new_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Move physical file
                if old_path.exists():
                    try:
                        if not force and new_path.exists():
                            logger.warning("Destination exists, skipping: %s", new_path)
                            errors += 1
                            continue
                        
                        shutil.move(str(old_path), str(new_path))
                        moved += 1
                        logger.info("Moved: %s -> %s", old_path, new_path)
                    except Exception as exc:
                        logger.error("Failed to move %s: %s", old_path, exc)
                        errors += 1
                        continue
                else:
                    logger.warning("Source file not found: %s", old_path)
                    # Still update DB path so future runs don't try again
                
                # Update database with new path (POSIX format for cross-platform)
                new_posix = new_path.as_posix()
                conn.execute(
                    "UPDATE generated_shorts SET local_path = ? WHERE id = ?",
                    (new_posix, m['id'])
                )
                updated += 1
            
            conn.commit()
            logger.info("Database updated for %d records", updated)
            
    except Exception as exc:
        logger.error("Database transaction failed: %s", exc)
        errors += len(migrations) - updated
        return (moved, updated, errors)
    
    # Clean up empty legacy directories
    try:
        for video_dir in shorts_dir.iterdir():
            if video_dir.is_dir() and video_dir != shorts_dir:
                # Check if it's a legacy video title directory (no niche subdir)
                has_subdirs = any(d.is_dir() for d in video_dir.iterdir())
                if not has_subdirs:
                    # Check if empty
                    if not any(video_dir.iterdir()):
                        video_dir.rmdir()
                        logger.info("Removed empty legacy directory: %s", video_dir)
    except Exception as exc:
        logger.warning("Error cleaning up empty directories: %s", exc)
    
    return (moved, updated, errors)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate legacy shorts directory structure to niche-organized layout"
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be migrated without making changes'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='Overwrite existing files at destination'
    )
    args = parser.parse_args()
    
    logger.info("Starting shorts structure migration (dry_run=%s, force=%s)", 
                args.dry_run, args.force)
    
    moved, updated, errors = migrate_shorts(dry_run=args.dry_run, force=args.force)
    
    logger.info("Migration complete: %d moved, %d updated, %d errors", 
                moved, updated, errors)
    
    if errors:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()