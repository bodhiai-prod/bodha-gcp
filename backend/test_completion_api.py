"""One current completion result governs detail, library and exports."""
import asyncio
import copy
from unittest.mock import AsyncMock, patch
import pytest
import server
from fastapi import HTTPException
from paper_status import library_status_for_paper
from test_paper_completion import paper_fixture


def test_api_library_and_export_agree_for_every_mode():
    async def run():
        for mode in ('normal', 'template', 'competitive'):
            paper = paper_fixture()
            paper.update({'generation_status': 'ready', 'is_competitive': mode == 'competitive',
                'completion': {'status': 'running', 'ready': False, 'export_eligible': False,
                    'actual_scored_parts': 39, 'target_scored_parts': 41, 'valid_scored_parts': 37,
                    'failed_gates': ['4 scored parts remain incomplete.']},
                'quality_review': {'status': 'running'}})
            summary, check, _ = server._current_paper_validation(paper)
            assert summary['actual_scored_part_count'] == 39
            assert not check['honoured']
            assert library_status_for_paper(paper)['active']
            db = type('DB', (), {'papers': type('P', (), {'find_one': AsyncMock(return_value=paper), 'update_one': AsyncMock()})()})()
            with patch.object(server, 'db', db), patch.object(server, 'get_token_user', AsyncMock(return_value={'id': 'teacher', 'role': 'teacher'})):
                with pytest.raises(HTTPException) as error:
                    await server.paper_pdf('paper', authorization='Bearer test', auth=None)
                assert error.value.status_code == 409
            db.papers.update_one.assert_not_awaited()
    asyncio.run(run())


def test_all_continuation_urls_queue_the_same_coordinator():
    async def run():
        paper = paper_fixture()
        db = type('DB', (), {'papers': type('P', (), {'find_one': AsyncMock(return_value=paper)})()})()
        with patch.object(server, 'db', db), patch.object(server, '_paper_generation_inputs', AsyncMock(return_value=('', ''))), \
             patch('completion_store.enqueue_continuation', AsyncMock(return_value={'status': 'queued'})) as enqueue, \
             patch.object(server, '_spawn') as spawn:
            user = {'id': 'teacher', 'role': 'teacher'}
            await server.generate_missing_questions('paper', {}, user)
            await server.retry_template_recovery('paper', user)
            await server.repair_paper_internal_choices('paper', user)
            assert enqueue.await_count == 3
            spawn.assert_not_called()
    asyncio.run(run())


def test_reading_paper_never_starts_or_persists_recovery():
    async def run():
        paper = paper_fixture()
        paper['generation_recovery'] = {'status': 'queued'}
        papers = type('P', (), {'find_one': AsyncMock(return_value=paper), 'update_one': AsyncMock()})()
        class Textbooks:
            def find(self, *args):
                return self
            async def to_list(self, *args):
                return []
        with patch.object(server, 'db', type('DB', (), {'papers': papers, 'textbooks': Textbooks()})()), patch.object(server, '_spawn') as spawn:
            await server.get_paper('paper', {'id': 'teacher', 'role': 'teacher'})
        papers.update_one.assert_not_awaited()
        spawn.assert_not_called()
    asyncio.run(run())
