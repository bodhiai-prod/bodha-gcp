"""The first teacher-visible draft must contain only safe question blocks."""
import workers


def question(**overrides):
    value = {
        "id": "q1",
        "question": "State one property of a parallelogram.",
        "format": "short_answer",
        "type": "information",
        "difficulty": "easy",
        "marks": 1,
        "options": [],
    }
    value.update(overrides)
    return value


def sections(*questions):
    return [{"section_id": "A", "title": "Section A", "questions": list(questions)}]


def test_screen_keeps_complete_renderable_question_and_withholds_bad_mcq():
    good = question()
    incomplete = question(
        id="q2", question="Which is a prime number?", format="mcq",
        options=["2", "3"], correct_option=0,
    )

    visible, issues = workers._screen_viewable_sections(
        sections(good, incomplete), spec={}
    )

    assert [q["id"] for q in visible[0]["questions"]] == ["q1"]
    assert any("mcq_missing_options" in issue for issue in issues)


def test_screen_withholds_unbalanced_math_before_first_view():
    visible, issues = workers._screen_viewable_sections(
        sections(question(question="Simplify $x^2 + 2x.")), spec={}
    )

    assert visible == []
    assert any("unbalanced_delims" in issue for issue in issues)


def test_screen_withholds_question_that_does_not_match_its_server_slot():
    plan = {
        "slots": [{
            "slot_id": "A-1", "section_id": "A", "topic": "Algebra",
            "type": "information", "format": "short_answer",
            "difficulty": "easy", "marks": 1,
        }]
    }
    visible, issues = workers._screen_viewable_sections(
        sections(question(slot_id="A-2", section_id="A", topic="Algebra")),
        spec={}, normal_plan=plan,
    )

    assert visible == []
    assert any("paper slot" in issue for issue in issues)


def test_screen_keeps_complete_stems_with_pending_required_or_group():
    q = question(
        allocation_id="allocation-1", section_id="A", group_id="g1",
        internal_choice_count=0, textbook_id="book", topic_id="topic", slot_id="slot", topic="Algebra",
    )
    spec = {
        "section_allocations": [{
            "allocation_id": "allocation-1", "section_id": "A",
            "group_id": "g1", "name": "Algebra", "textbook_id": "book",
            "topic_id": "topic", "slot_id": "slot", "marks": 1,
            "format": "short_answer", "difficulty": "easy",
        }],
        "blueprint": {"sections": [{
            "id": "A", "groups": [{"id": "g1", "internal_choices": 1}],
        }]},
    }

    visible, issues = workers._screen_viewable_sections(
        [{"section_id": "A", "title": "Section A", "questions": [q]}],
        spec=spec,
    )

    assert len(visible[0]["questions"]) == 1
    assert visible[0]["questions"][0]["completion_pending"]
    assert any("internal choices" in issue for issue in issues)
