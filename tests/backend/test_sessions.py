from uuid import uuid4

from backend.sessions import SessionStore
from steel_slag_slm.rag.similar_slags import SlagProfile


def test_get_or_create_returns_the_same_session_for_the_same_id() -> None:
    store = SessionStore()
    session_id = uuid4()

    assert store.get_or_create(session_id) is store.get_or_create(session_id)
    assert store.get_or_create(session_id) is not store.get_or_create(uuid4())


def test_set_profile_replaces_the_earlier_profile() -> None:
    store = SessionStore()
    session_id = uuid4()
    store.set_profile(session_id, SlagProfile(steel_slag_type="EAF", oxides={"cao_pct": 30}))

    store.set_profile(session_id, SlagProfile(steel_slag_type="BOF", oxides={"cao_pct": 45}))

    profile = store.get_or_create(session_id).profile
    assert profile is not None
    assert profile.steel_slag_type == "BOF"
