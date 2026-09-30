"""Content dependencies shared by completion publication and solution workers."""
from copy import deepcopy

CONTENT_FIELDS = ('question', 'options', 'correct_option', 'diagram_spec', 'diagram_path', 'parent_lead_in')


def sources(sections):
    return {q.get('id'): {key: q.get(key) for key in CONTENT_FIELDS}
            for section in sections or [] for q in section.get('questions') or []}


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
