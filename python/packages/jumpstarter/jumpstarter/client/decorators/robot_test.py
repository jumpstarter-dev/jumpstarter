from jumpstarter.client.decorators import robot
from jumpstarter.client.decorators._frontend import frontend_meta


def test_keyword_bare_and_called():
    @robot.keyword
    def bare():
        pass

    @robot.keyword("Should Be On", tags=["assert"], doc="Fail unless on.")
    def named():
        pass

    # vars(): the attributes are set on the function object itself
    assert vars(bare)["robot_name"] is None
    assert vars(bare)["robot_tags"] == ()
    assert not hasattr(bare, "robot_doc")
    assert vars(named)["robot_name"] == "Should Be On"
    assert vars(named)["robot_tags"] == ("assert",)
    assert vars(named)["robot_doc"] == "Fail unless on."
    assert frontend_meta(named, "robot")["name"] == "Should Be On"


def test_not_keyword():
    @robot.not_keyword
    def hidden():
        pass

    assert vars(hidden)["robot_not_keyword"] is True
    assert frontend_meta(hidden, "robot") == {"hidden": True}


def test_library_defaults_like_robot_framework():
    @robot.library
    class Bare:
        pass

    @robot.library(auto_keywords=True, doc="Intro")
    class Auto:
        pass

    assert vars(Bare)["ROBOT_AUTO_KEYWORDS"] is False
    assert vars(Auto)["ROBOT_AUTO_KEYWORDS"] is True
    assert vars(Auto)["ROBOT_LIBRARY_DOC"] == "Intro"


def test_library_metadata_is_not_inherited_by_mutation():
    @robot.library(auto_keywords=True)
    class Base:
        pass

    @robot.library
    class Child(Base):
        pass

    assert frontend_meta(Base, "robot")["auto_keywords"] is True
    assert frontend_meta(Child, "robot")["auto_keywords"] is False


def test_frontend_meta_missing():
    assert frontend_meta(object(), "robot") == {}
