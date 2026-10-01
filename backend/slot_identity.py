"""Server-owned slot contract, separate from teacher and execution state."""
SLOT_IDENTITY_FIELDS = (
    'id', 'slot_id', 'section_id', 'section_title', 'topic', 'parent_topic',
    'topic_id', 'textbook_id', 'type', 'format', 'difficulty', 'marks',
    'allocation_id', 'group_id', 'parent_id', 'parent_label', 'part_label',
    'parent_lead_in', 'case_study', 'case_profile', 'requires_shared_stimulus', 'display_number', 'internal_choice_count', 'order', 'diagram_mode',
)
TEACHER_FIELDS = ('important',)


def restore_missing_identity(question, slot):
    """Use only after the caller has proved the slot/allocation match."""
    from copy import deepcopy
    for field in SLOT_IDENTITY_FIELDS:
        if question.get(field) is None and field in slot:
            question[field] = deepcopy(slot[field])
    return question
