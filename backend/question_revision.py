"""Content dependencies shared by completion publication and solution workers."""
from copy import deepcopy

CONTENT_FIELDS = ('question', 'options', 'correct_option', 'diagram_spec', 'diagram_path', 'parent_lead_in', 'shared_stimulus')


def sources(sections):
    """Fingerprint each child with its parent passage when one exists.

    A passage may be stored on any sibling in legacy documents, so discover it
    by parent rather than assuming it remains on the first visible child.
    """
    questions = [q for section in sections or [] for q in section.get('questions') or []]
    passages = {}
    for q in questions:
        if q.get('parent_id') and q.get('case_study') and q.get('shared_stimulus'):
            passages[(q.get('section_id'), q.get('parent_id'))] = q.get('shared_stimulus')
    return {q.get('id'): {**{key: q.get(key) for key in CONTENT_FIELDS},
                           '_parent_stimulus': passages.get((q.get('section_id'), q.get('parent_id')))}
            for q in questions}


def invalidate_changed_solution(paper, sections):
    solution = paper.get('solution')
    if not solution:
        return None
    before, after = sources(paper.get('sections')), sources(sections)
    changed = {qid for qid in before if before[qid] != after.get(qid)}
    if not changed:
        return None
    solution = deepcopy(solution)
    for section in solution.get('sections') or []:
        for answer in section.get('answers') or []:
            if answer.get('question_id') in changed:
                answer['answer'] = ''
    solution['completed'] = sum(bool(a.get('answer')) for s in solution.get('sections') or [] for a in s.get('answers') or [])
    solution['is_stale'] = True
    if solution.get('status') == 'ready':
        solution['status'] = 'partial'
    return solution
