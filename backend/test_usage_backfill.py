import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from usage_backfill import backfill


class Collection:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def find(self, *args):
        return self

    async def to_list(self, *args):
        return self.rows.copy()

    async def insert_one(self, row):
        self.rows.append(row)


class BackfillTests(unittest.TestCase):
    def test_repeat_import_preserves_identical_calls_without_duplicates(self):
        class DB:
            papers = Collection([{'id': 'paper-1', 'owner_id': 'teacher', 'generation_status': 'completed'}])
            textbooks = Collection([{'id': 'book-1'}])
            usage_operations = Collection()
            usage_calls = Collection()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usage.csv'
            path.write_text('timestamp,kind,ref,model_id,input_tokens,output_tokens,total_tokens,latency_ms\n'
                            + '2026-01-01,paper,paper-1,model,10,20,30,500\n' * 2)
            with patch.dict('os.environ', {'TOKEN_USAGE_CSV': str(path)}):
                first = asyncio.run(backfill(DB))
                second = asyncio.run(backfill(DB))
            self.assertEqual(first['calls_imported'], 2)
            self.assertEqual(second['calls_imported'], 0)
            self.assertEqual(second['operations_created'], 0)
            self.assertEqual(len(DB.usage_operations.rows), 2)
            self.assertIsNone(DB.usage_operations.rows[0]['active_ms'])


if __name__ == '__main__':
    unittest.main()
