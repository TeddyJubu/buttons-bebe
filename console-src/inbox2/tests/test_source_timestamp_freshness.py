"""Timestamp-domain regressions for cached Gorgias detail versus synced summaries."""
from contextlib import closing
from pathlib import Path
import json
import sys
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_detail_preview_freshness as detail_setup
import live_api as api


class SourceTimestampFreshness(unittest.TestCase):
    """Reuse the real API/private-SQLite setup from the detail-preview tests."""

    def setUp(self):
        self.fixture = detail_setup.DetailPreviewFreshness('test_fresh_head_is_not_stale_and_is_cached')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def assertNoFreshDetailCache(self):
        entry = api.DETAIL_CACHE.get('841999118')
        self.assertTrue(entry is None or (entry[1].get('syncStale') is True
                                          and entry[1].get('draftSuperseded') is True))

    def test_naive_updated_metadata_cannot_replace_closed_aware_summary(self):
        self.fixture.sync(detail_setup.raw(detail_setup.T7, 'Stored clean summary', '2026-10-05T07:30:00Z', status='closed'))
        before = self.fixture.stored()
        internal_note = {
            'id': 842000120, 'display_text': 'Staff note', 'created_datetime': detail_setup.T7,
            'channel': 'internal-note', 'public': False,
        }
        detail = detail_setup.raw(detail_setup.T7, 'Lagging detail excerpt', '2026-10-05T20:00:00', status='open')

        out = self.fixture.detail(detail, [detail_setup.OLD, internal_note])

        self.fixture.assertStale(out)
        self.assertEqual((out['status'], out['lastMessageAt']), ('open', detail_setup.T7))
        self.assertEqual(self.fixture.stored(), before)
        self.assertNoFreshDetailCache()
        self.assertEqual(detail_setup.FakeMCP.calls, ['get_ticket', 'get_ticket_messages'])

    def test_invalid_updated_metadata_is_held_without_a_prior_summary(self):
        for updated in (None, 'not-a-time', '2026-10-05T20:00:00', '2026-10-06'):
            with self.subTest(updated=updated):
                detail = detail_setup.raw(detail_setup.T7, 'Candidate with untrusted metadata', detail_setup.T7)
                if updated is None:
                    detail.pop('updated_datetime')
                    detail['created_datetime'] = detail_setup.T7
                else:
                    detail['updated_datetime'] = updated
                latest = {
                    'id': 842000122, 'display_text': 'Candidate public message',
                    'created_datetime': detail_setup.T7, 'channel': 'email', 'public': True,
                }

                out = self.fixture.detail(detail, [latest])

                self.fixture.assertStale(out)
                with closing(api.database()) as db:
                    self.assertIsNone(db.execute('SELECT 1 FROM tickets WHERE id=?',
                                                 ('gorgias:841999118',)).fetchone())
                self.assertNoFreshDetailCache()

    def test_future_naive_or_date_only_activity_is_held_stale(self):
        for activity in ('2026-10-05T20:00:00', '2026-10-06'):
            with self.subTest(activity=activity):
                self.fixture.sync(detail_setup.raw(detail_setup.T7, 'Stored newer activity', detail_setup.T7, status='closed'))
                before = self.fixture.stored()
                detail = detail_setup.raw(activity, 'Older public message cannot cover this activity', detail_setup.T7, status='open')

                out = self.fixture.detail(detail, [detail_setup.OLD])

                self.fixture.assertStale(out)
                self.assertNotIn('previewMessageId', out)
                self.assertEqual(self.fixture.stored(), before)
                self.assertNoFreshDetailCache()

    def test_invalid_stored_activity_or_metadata_never_returns_cached_draft_as_fresh(self):
        for field, invalid in (('lastMessageAt', 'not-a-time'), ('updatedAt', 'not-a-time')):
            with self.subTest(field=field):
                detail_setup.FakeMCP.fail = False
                api.DETAIL_CACHE.clear()
                self.fixture.sync(detail_setup.raw(detail_setup.T6, 'Known clean summary', detail_setup.T6, status='open'))
                clean = self.fixture.detail(detail_setup.raw(detail_setup.T6, 'Known clean summary', detail_setup.T6), [detail_setup.OLD])
                self.assertEqual((clean['syncStale'], clean['draftSuperseded']), (False, False))
                cached_entry = api.DETAIL_CACHE['841999118']
                before, generation = self.fixture.stored()
                corrupted = dict(before)
                corrupted[field] = invalid
                with closing(api.database()) as db, db:
                    db.execute('UPDATE tickets SET payload=? WHERE id=?',
                               (json.dumps(corrupted), 'gorgias:841999118'))
                stored_invalid = self.fixture.stored()
                detail_setup.FakeMCP.calls = []
                detail_setup.FakeMCP.fail = True
                try:
                    out = api.get_ticket(841999118)
                    self.fixture.assertStale(out)
                    self.assertIn(detail_setup.FakeMCP.calls, ([], ['get_ticket']))
                    current_entry = api.DETAIL_CACHE.get('841999118')
                    if current_entry is not None:
                        self.assertIs(current_entry, cached_entry)
                    self.assertEqual((cached_entry[1]['syncStale'], cached_entry[1]['draftSuperseded']), (False, False))
                    self.assertEqual(self.fixture.stored(), stored_invalid)
                    self.assertEqual(self.fixture.stored()[1], generation)
                finally:
                    detail_setup.FakeMCP.fail = False
                    api.DETAIL_CACHE.clear()

    def test_offset_equivalent_aware_instants_remain_fresh(self):
        self.fixture.sync(detail_setup.raw(detail_setup.T7, 'Stored current summary', '2026-10-05T07:30:00Z', status='open'))
        detail = detail_setup.raw('2026-10-05T13:00:00+06:00', 'Offset-equivalent detail',
                     '2026-10-05T13:30:00+06:00', status='open')
        latest = {
            'id': 842000121, 'display_text': 'Offset-equivalent current public message',
            'created_datetime': '2026-10-05T13:00:00+06:00', 'channel': 'email', 'public': True,
        }

        out = self.fixture.detail(detail, [latest])

        self.assertEqual((out['syncStale'], out['draftSuperseded']), (False, False))
        self.assertEqual(out['lastMessageAt'], '2026-10-05T13:00:00+06:00')
        self.assertEqual(out['previewMessageId'], '842000121')
        self.assertEqual(out['snippet'], latest['display_text'])

    def test_incremental_sync_keeps_paging_after_unknown_updated_timestamp(self):
        now = api.source_epoch('2026-10-05T12:00:00Z')
        with closing(api.database()) as db, db:
            meta = api.get_meta(db)
            meta.update(complete=True, generation='g1', fullAt=now,
                        watermark=api.source_epoch(detail_setup.T7))
            meta.pop('pendingFull', None)
            api.set_meta(db, meta)

        class StopFlag:
            def is_set(self):
                return False
            def wait(self, _timeout):
                return False

        class Worker:
            def __init__(self):
                self.stop = StopFlag()
            def update(self, *_args, **_kwargs):
                pass

        class PagedListMCP:
            calls = []
            pages = [
                {'data': [{'id': 841999118, 'status': 'open',
                           'last_message_datetime': detail_setup.T6,
                           'updated_datetime': '2026-10-04T00:00:00'}],
                 'meta': {'next_cursor': 'page-2'}},
                {'data': [{'id': 841999119, 'status': 'open',
                           'last_message_datetime': '2026-10-05T08:00:00Z',
                           'updated_datetime': '2026-10-05T08:00:00Z'}],
                 'meta': {'next_cursor': None}},
            ]
            def __init__(self, _worker=None):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def call(self, name, args):
                self.__class__.calls.append((name, dict(args)))
                return self.__class__.pages[len(self.__class__.calls) - 1]

        with patch.object(api.time, 'time', return_value=now), patch.object(api, 'MCP', PagedListMCP):
            api.sync_once(Worker())

        self.assertEqual(PagedListMCP.calls, [
            ('list_inbox_tickets', {'limit': 100}),
            ('list_inbox_tickets', {'limit': 100, 'cursor': 'page-2'}),
        ])
        with closing(api.database()) as db:
            rows = {row['id']: json.loads(row['payload'])
                    for row in db.execute('SELECT id,payload FROM tickets')}
            meta = api.get_meta(db)
        self.assertEqual(set(rows), {'gorgias:841999118', 'gorgias:841999119'})
        self.assertEqual(rows['gorgias:841999118']['updatedAt'], '2026-10-04T00:00:00')
        self.assertEqual(rows['gorgias:841999119']['lastMessageAt'], '2026-10-05T08:00:00Z')
        self.assertTrue(meta['complete'])
        self.assertNotIn('pendingFull', meta)
        self.assertEqual(meta['watermark'], api.source_epoch('2026-10-05T08:00:00Z'))


if __name__ == '__main__':
    unittest.main()
