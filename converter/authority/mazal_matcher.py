"""Mazal Authority Matcher Service.

Provides matching services to resolve entity names to NLI authority IDs.
Includes fuzzy matching and disambiguation strategies.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any

from .mazal_index import MazalIndex

logger = logging.getLogger(__name__)


def _get_default_index_path() -> Path:
    """Get the default Mazal index path, handling PyInstaller bundles."""
    # Check for PyInstaller bundle
    if getattr(sys, 'frozen', False):
        # Running in PyInstaller bundle - try multiple possible locations
        possible_paths = []
        
        # Try _MEIPASS (extracted temp folder)
        if hasattr(sys, '_MEIPASS'):
            base_path = Path(sys._MEIPASS)
            possible_paths.append(base_path / 'converter' / 'authority' / 'mazal_index.db')
        
        # Try relative to executable (macOS .app bundle)
        exe_path = Path(sys.executable)
        # For .app bundles: /path/to/App.app/Contents/MacOS/App -> /path/to/App.app/Contents/Resources
        if 'Contents/MacOS' in str(exe_path):
            resources_path = exe_path.parent.parent / 'Resources'
            possible_paths.append(resources_path / 'converter' / 'authority' / 'mazal_index.db')
        
        for index_path in possible_paths:
            if index_path.exists():
                logger.info(f"Mazal index found at: {index_path}")
                return index_path
            else:
                logger.debug(f"Mazal index not at: {index_path}")
        
        logger.warning(f"Mazal index not found in bundle. Tried: {possible_paths}")
    
    # Development mode - use path relative to this file
    dev_path = Path(__file__).parent / 'mazal_index.db'
    if dev_path.exists():
        logger.info(f"Mazal index found (dev mode): {dev_path}")
    return dev_path


class MazalMatcher:
    """Service for matching entities to Mazal authority records.
    
    This service wraps MazalIndex and provides:
    - Convenient lookup methods for each entity type
    - Match statistics tracking
    - Configurable fallback behavior
    """
    
    def __init__(self, index_path: str = None, track_stats: bool = True):
        """Initialize the matcher.
        
        Args:
            index_path: Path to the SQLite index file. If None, uses default location.
            track_stats: Whether to track matching statistics
        """
        if index_path is None:
            # Compute path lazily to handle PyInstaller correctly
            index_path = str(_get_default_index_path())
        
        self.index_path = index_path
        self.track_stats = track_stats
        self._index: Optional[MazalIndex] = None
        self._available: Optional[bool] = None
        
        # Statistics
        self._stats: Dict[str, Dict[str, int]] = {
            'person': {'matched': 0, 'unmatched': 0},
            'place': {'matched': 0, 'unmatched': 0},
            'work': {'matched': 0, 'unmatched': 0},
            'corporate': {'matched': 0, 'unmatched': 0}
        }
    
    @property
    def is_available(self) -> bool:
        """Check if the Mazal index is available."""
        if self._available is None:
            self._available = os.path.exists(self.index_path)
        return self._available
    
    @property
    def index(self) -> Optional[MazalIndex]:
        """Get the index instance (lazy loading)."""
        if self._index is None and self.is_available:
            try:
                self._index = MazalIndex(self.index_path)
            except Exception as e:
                logger.warning(f"Failed to load Mazal index: {e}")
                self._available = False
        return self._index
    
    def close(self):
        """Close the index connection."""
        if self._index:
            self._index.close()
            self._index = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def _update_stats(self, entity_type: str, matched: bool):
        """Update matching statistics."""
        if self.track_stats:
            key = 'matched' if matched else 'unmatched'
            self._stats[entity_type][key] += 1
    
    def match_person(self, name: str, dates: str = None) -> Optional[str]:
        """Match a person name to an NLI authority ID.
        
        Args:
            name: Person's name (Hebrew or Latin)
            dates: Optional date string for disambiguation (e.g., "1138-1204")
            
        Returns:
            NLI authority ID or None if no match found
        """
        if not self.is_available or not name:
            if name:
                self._update_stats('person', False)
            return None
        
        result = self.index.lookup_person(name, dates)
        self._update_stats('person', result is not None)
        return result
    
    def match_place(self, name: str) -> Optional[str]:
        """Match a place name to an NLI authority ID.
        
        Args:
            name: Place name (Hebrew or Latin)
            
        Returns:
            NLI authority ID or None if no match found
        """
        if not self.is_available or not name:
            if name:
                self._update_stats('place', False)
            return None
        
        result = self.index.lookup_place(name)
        self._update_stats('place', result is not None)
        return result
    
    def match_work(self, title: str, author: str = None) -> Optional[str]:
        """Match a work title to an NLI authority ID.
        
        Args:
            title: Work title (Hebrew or Latin)
            author: Optional author name for disambiguation
            
        Returns:
            NLI authority ID or None if no match found
        """
        if not self.is_available or not title:
            if title:
                self._update_stats('work', False)
            return None
        
        # For now, just use title lookup
        # Future enhancement: combine with author for disambiguation
        result = self.index.lookup_work(title)
        self._update_stats('work', result is not None)
        return result
    
    def match_corporate(self, name: str) -> Optional[str]:
        """Match a corporate body name to an NLI authority ID.
        
        Args:
            name: Corporate body name (Hebrew or Latin)
            
        Returns:
            NLI authority ID or None if no match found
        """
        if not self.is_available or not name:
            if name:
                self._update_stats('corporate', False)
            return None
        
        result = self.index.lookup_corporate(name)
        self._update_stats('corporate', result is not None)
        return result
    
    def get_record(self, nli_id: str) -> Optional[dict]:
        """Get full authority record for an NLI ID.
        
        Args:
            nli_id: NLI authority identifier
            
        Returns:
            Dictionary with record data or None
        """
        if not self.is_available:
            return None
        return self.index.get_record(nli_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get matching statistics.
        
        Returns:
            Dictionary with statistics by entity type
        """
        total_matched = sum(s['matched'] for s in self._stats.values())
        total_unmatched = sum(s['unmatched'] for s in self._stats.values())
        total = total_matched + total_unmatched
        
        return {
            'by_type': self._stats.copy(),
            'total_matched': total_matched,
            'total_unmatched': total_unmatched,
            'total_attempts': total,
            'match_rate': total_matched / total if total > 0 else 0.0,
            'index_available': self.is_available
        }
    
    def reset_stats(self):
        """Reset matching statistics."""
        for entity_type in self._stats:
            self._stats[entity_type] = {'matched': 0, 'unmatched': 0}
    
    def get_unmatched_summary(self) -> str:
        """Get a human-readable summary of matching statistics."""
        stats = self.get_stats()
        
        lines = [
            "Mazal Authority Matching Summary",
            "=" * 40,
            f"Index available: {stats['index_available']}",
            f"Total attempts: {stats['total_attempts']:,}",
            f"Match rate: {stats['match_rate']:.1%}",
            "",
            "By entity type:",
        ]
        
        for entity_type, counts in stats['by_type'].items():
            total = counts['matched'] + counts['unmatched']
            rate = counts['matched'] / total if total > 0 else 0
            lines.append(f"  {entity_type}: {counts['matched']:,}/{total:,} ({rate:.1%})")
        
        return "\n".join(lines)


    def get_person_details(self, nli_id: str) -> dict[str, str]:
        """Get detailed person information from the Mazal authority DB.

        Args:
            nli_id: NLI authority ID (mazal_id).

        Returns:
            Dict with dates, preferred_name_heb, preferred_name_lat, aleph_id.
        """
        if not self.is_available or not self._index:
            return {}
        try:
            conn = self._index._conn
            row = conn.execute(
                "SELECT dates, preferred_name_heb, preferred_name_lat, aleph_id "
                "FROM authorities WHERE nli_id = ?",
                (nli_id,),
            ).fetchone()
            if row:
                return {
                    "dates": row[0] or "",
                    "preferred_name_heb": row[1] or "",
                    "preferred_name_lat": row[2] or "",
                    "aleph_id": row[3] or "",
                }
        except Exception as exc:
            logger.debug("Failed to get details for %s: %s", nli_id, exc)
        return {}


def create_matcher(index_path: str = None) -> Optional[MazalMatcher]:
    """Create a MazalMatcher instance if the index exists.
    
    Args:
        index_path: Optional path to the index file
        
    Returns:
        MazalMatcher instance or None if index doesn't exist
    """
    matcher = MazalMatcher(index_path)
    if matcher.is_available:
        return matcher
    logger.info("Mazal index not found - authority matching disabled")
    return None

