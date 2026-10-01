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


def test_stopped_draft_can_be_acknowledged_and_exported():
    async def run():
        paper = paper_fixture()
        paper.update({
            'title': 'Stopped draft',
            'generation_status': 'cancelled',
            'sections': [{'title': 'A', 'questions': [{'id': 'q1', 'question': 'Q?', 'marks': 1}]}],
            'completion': {'status': 'cancelled', 'ready': False, 'export_eligible': False},
            'quality_review': {'status': 'cancelled'},
        })
        acknowledged = copy.deepcopy(paper)
        acknowledged['export_warning_acknowledgement'] = {
            'ignored': True, 'acknowledged_by': 'teacher', 'acknowledged_at': 'now'
        }
        papers = type('P', (), {
            'find_one': AsyncMock(side_effect=[paper, acknowledged]),
            'update_one': AsyncMock(),
        })()
        db = type('DB', (), {'papers': papers})()
        with patch.object(server, 'db', db), patch.object(server, 'get_paper', AsyncMock(return_value=acknowledged)):
            result = await server.ignore_paper_export_warning('paper', {'id': 'teacher', 'role': 'teacher'})
        assert result['export_warning_acknowledgement']['ignored']
        saved = papers.update_one.await_args.args[1]['$set']['export_warning_acknowledgement']
        assert saved['ignored'] and saved['acknowledged_by'] == 'teacher'

        export_db = type('DB', (), {'papers': type('P', (), {'find_one': AsyncMock(return_value=acknowledged)})()})()
        with patch.object(server, 'db', export_db), \
             patch.object(server, 'get_token_user', AsyncMock(return_value={'id': 'teacher', 'role': 'teacher'})), \
             patch.object(server, '_normalize_saved_paper_sections', return_value=(acknowledged['sections'], None, False)), \
             patch.object(server, 'render_paper_pdf_async', AsyncMock(return_value=b'pdf')):
            response = await server.paper_pdf('paper', authorization='Bearer test', auth=None)
        assert response.body == b'pdf'

        export_db = type('DB', (), {'papers': type('P', (), {'find_one': AsyncMock(return_value=acknowledged)})()})()
        with patch.object(server, 'db', export_db), \
             patch.object(server, 'get_token_user', AsyncMock(return_value={'id': 'teacher', 'role': 'teacher'})), \
             patch.object(server, '_normalize_saved_paper_sections', return_value=(acknowledged['sections'], None, False)), \
             patch.object(server, 'render_paper_docx_async', AsyncMock(return_value=b'docx')):
            response = await server.paper_docx('paper', authorization='Bearer test', auth=None)
        assert response.body == b'docx'
    asyncio.run(run())


def test_export_warning_cannot_be_ignored_while_active_or_empty_or_by_another_user():
    async def run():
        base = paper_fixture()
        cases = [
            ({**base, 'generation_status': 'generating', 'sections': [{'questions': [{'id': 'q1'}]}]}, 409),
            ({**base, 'generation_status': 'cancelled', 'sections': []}, 409),
        ]
        for paper, expected in cases:
            db = type('DB', (), {'papers': type('P', (), {
                'find_one': AsyncMock(return_value=paper), 'update_one': AsyncMock()
            })()})()
            with patch.object(server, 'db', db):
                with pytest.raises(HTTPException) as error:
                    await server.ignore_paper_export_warning('paper', {'id': 'teacher', 'role': 'teacher'})
                assert error.value.status_code == expected
                db.papers.update_one.assert_not_awaited()

        owned = {**base, 'generation_status': 'cancelled', 'sections': [{'questions': [{'id': 'q1'}]}]}
        db = type('DB', (), {'papers': type('P', (), {
            'find_one': AsyncMock(return_value=owned), 'update_one': AsyncMock()
        })()})()
        with patch.object(server, 'db', db):
            with pytest.raises(HTTPException) as error:
                await server.ignore_paper_export_warning('paper', {'id': 'other', 'role': 'teacher'})
            assert error.value.status_code == 403
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


def test_paper_reads_upgrade_generated_summaries_but_preserve_explicit_edits():
    async def run():
        for edited in (False, True):
            paper = paper_fixture()
            paper['generation_requirements']['blueprint']['id'] = 'isc12-mathematics'
            paper['generation_requirements']['blueprint']['student_instructions'] = 'Old summary.'
            paper['instructions'] = 'Saved directions.'
            paper['instructions_edited'] = edited
            papers = type('P', (), {'find_one': AsyncMock(return_value=paper), 'update_one': AsyncMock()})()
            class Textbooks:
                def find(self, *args):
                    return self
                async def to_list(self, *args):
                    return []
            with patch.object(server, 'db', type('DB', (), {'papers': papers, 'textbooks': Textbooks()})()):
                result = await server.get_paper('paper', {'id': 'teacher', 'role': 'teacher'})
            if edited:
                assert result['instructions'] == 'Saved directions.'
            else:
                assert '12. Mathematical tables' in result['instructions']
                assert 'Old summary.' not in result['instructions']
            papers.update_one.assert_not_awaited()
    asyncio.run(run())


def test_only_changed_teacher_instructions_are_marked_as_edited():
    async def run():
        for changed in (False, True):
            paper = paper_fixture()
            paper['generation_requirements']['blueprint']['id'] = 'isc12-mathematics'
            paper['instructions'] = 'Generated summary.'
            displayed = server.printable_template_instructions(paper)
            papers = type('P', (), {'find_one': AsyncMock(return_value=paper), 'update_one': AsyncMock()})()
            with patch.object(server, 'db', type('DB', (), {'papers': papers})()), \
                 patch.object(server, 'get_paper', AsyncMock(return_value=paper)):
                await server.update_paper('paper', server.PaperUpdate(
                    instructions='Teacher directions.' if changed else displayed), {'id': 'teacher', 'role': 'teacher'})
            fields = papers.update_one.call_args.args[1]['$set']
            assert fields.get('instructions_edited', False) == changed
    asyncio.run(run())
