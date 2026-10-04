from app.main import _public_agent_reply


def test_draft_reply_hides_internal_action_metadata_and_explains_approval():
    text = _public_agent_reply(
        "draft_message",
        "Draft ready",
        {"audience": "team", "body": "Hi team! The event is next week."},
        pending_id=12,
    )

    assert text.startswith("@agent Draft for team is ready:")
    assert "[draft_message|medium]" not in text
    assert "Hi team! The event is next week." in text
    assert "#12" in text
    assert "organizer" in text.lower()
